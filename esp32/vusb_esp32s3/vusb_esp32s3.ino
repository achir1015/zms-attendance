/*
  虛擬 U 盤（ESP32-S3）— 資料助秘刷卡考勤系統
  ------------------------------------------------------------
  插在考勤機的 U 盤插口（用板子的「USB」原生接口），考勤機會看到一顆 FAT32 64MB 的 U 盤
  （稀疏儲存：只有寫過的磁區佔空間，實際可存 6MB）。板子一直插在考勤機上，不必拿下來：
  * 設定網頁：同一個網路開 http://板子IP/（Wi-Fi 可從掃描清單選，存檔同步寫回 U 盤的 設定.txt）；
    開機 60 秒連不上 Wi-Fi（或尚未設定）會開熱點 ZMS-VUSB-xxxx（密碼 zmsvusb1），連上後開 http://192.168.4.1
  * 韌體更新：考勤系統有較新的韌體時，板子自己下載更新、重新開機
  * 診斷：回報考勤機有沒有掛上 U 盤、讀寫次數、Wi-Fi 訊號（顯示在考勤系統「虛擬 U 盤」頁）
  * 考勤機匯出 GLG_xxx.TXT／AGL_xxx.TXT 後，靜止 4 秒 → 模擬拔出 → 經 Wi-Fi 上傳到考勤系統
    → 系統回覆 ok 就刪掉檔案 → 模擬插回。
  * 每 15 秒向考勤系統回報狀態；系統有待送的人員資訊表時，同樣拔出、放進檔案、插回。
  * 設定：插到電腦，編輯 U 盤裡的「設定.txt」存檔即可（Wi-Fi 密碼存好後會隱藏）。
  除錯訊息：板子的「COM」接口（CH340），115200。
  編譯：esp32 core 3.3.x，FQBN esp32:esp32:esp32s3:PSRAM=opi,FlashSize=16M,USBMode=default,CDCOnBoot=default
*/
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <sys/time.h>
#include "USB.h"
#include "USBMSC.h"
#include "ff.h"
#include "diskio_impl.h"
#include "mbedtls/sha1.h"
#include "tusb.h"
#include <Update.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <vector>

// 明確宣告（含原始字串的檔案，Arduino 自動產生的宣告不可靠）
void formatDisk();
void refreshListing();
void syncNow(const std::vector<int> &pending);
void startWeb();
void startPortal();
void stopPortal();
void doOta();
bool versionNewer(const String &a, const String &b);
String baseUrl();
bool serverReady();
void saveConfig();
void writeSettingsFile();

#define FW_VERSION "1.3.1"
#define SECTOR_SIZE 512
#define SECTOR_COUNT 131072             // 對外宣稱 64MB（FAT32；只有寫過的磁區才佔空間）
#define POOL_SECTORS 12288              // 實際儲存空間 6MB（PSRAM）
#define IDLE_AFTER_WRITE_MS 4000
#define HELLO_INTERVAL_MS 15000
#define RETRY_INTERVAL_MS 60000
#define DETACH_SETTLE_MS 400
#ifndef RGB_BUILTIN
#define RGB_BUILTIN 48
#endif

static const char *SETTINGS_FILE = "0:/設定.txt";

USBMSC MSC;
Preferences prefs;
static FATFS fatfs;

// ------------------------------------------------------------ 稀疏磁碟：64MB 位址空間，只存寫過且非全 0 的磁區
static uint16_t *secmap = nullptr;       // 磁區 → 池中位置+1（0＝全 0）
static uint8_t *pool = nullptr;          // POOL_SECTORS × 512
static uint16_t *freeList = nullptr;
static volatile uint32_t freeTop = 0;
static portMUX_TYPE diskMux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool poolFull = false;

void diskReset() {
  memset(secmap, 0, sizeof(uint16_t) * SECTOR_COUNT);
  freeTop = 0;
  for (uint32_t i = 0; i < POOL_SECTORS; i++) freeList[freeTop++] = POOL_SECTORS - 1 - i;
  poolFull = false;
}
static bool allZero(const uint8_t *b) {
  const uint32_t *w = (const uint32_t *)b;
  for (int i = 0; i < SECTOR_SIZE / 4; i++) if (w[i]) return false;
  return true;
}
void secRead(uint32_t s, uint8_t *out) {
  uint16_t m = secmap[s];
  if (m) memcpy(out, pool + (size_t)(m - 1) * SECTOR_SIZE, SECTOR_SIZE);
  else memset(out, 0, SECTOR_SIZE);
}
bool secWrite(uint32_t s, const uint8_t *in) {
  uint16_t m = secmap[s];
  if (allZero(in)) {
    if (m) { portENTER_CRITICAL(&diskMux); freeList[freeTop++] = m - 1; secmap[s] = 0; portEXIT_CRITICAL(&diskMux); }
    return true;
  }
  if (!m) {
    portENTER_CRITICAL(&diskMux);
    if (freeTop == 0) { portEXIT_CRITICAL(&diskMux); poolFull = true; return false; }
    m = freeList[--freeTop] + 1;
    secmap[s] = m;
    portEXIT_CRITICAL(&diskMux);
  }
  memcpy(pool + (size_t)(m - 1) * SECTOR_SIZE, in, SECTOR_SIZE);
  return true;
}
uint32_t poolFreeBytes() { return freeTop * SECTOR_SIZE; }

struct Config {
  String ssid, pass, server, token, name;
  int port = 8796;
  int machineNo = 1;
} cfg;

static volatile uint32_t lastWriteMs = 0;
static volatile bool hostWrote = false;
static volatile bool hostEjected = false;
static bool attached = false;
static bool wantSync = false;
static volatile uint32_t readCount = 0, writeCount = 0, ejectCount = 0, mountCount = 0;
static uint32_t lastHello = 0, lastRetry = 0;
static String cachedFiles;               // JSON 陣列內容（不含括號）
static uint32_t freeBytes = 0, totalBytes = 0;
static bool hasExports = false;           // U 盤上有還沒傳回系統的檔案
static std::vector<String> placed;        // 系統送來、放進 U 盤的檔案（不要再傳回去）
static String lastStatus = "開機";
static uint32_t settingsHash = 0;

// ------------------------------------------------------------ LED 狀態
void led(uint8_t r, uint8_t g, uint8_t b) { rgbLedWrite(RGB_BUILTIN, r, g, b); }

// ------------------------------------------------------------ FatFs 對 RAM 碟的磁碟驅動
static DSTATUS rd_init(BYTE) { return 0; }
static DSTATUS rd_status(BYTE) { return 0; }
static DRESULT rd_read(BYTE, BYTE *buf, DWORD sector, UINT count) {
  if (sector + count > SECTOR_COUNT) return RES_PARERR;
  for (UINT i = 0; i < count; i++) secRead(sector + i, buf + (size_t)i * SECTOR_SIZE);
  return RES_OK;
}
static DRESULT rd_write(BYTE, const BYTE *buf, DWORD sector, UINT count) {
  if (sector + count > SECTOR_COUNT) return RES_PARERR;
  for (UINT i = 0; i < count; i++) if (!secWrite(sector + i, buf + (size_t)i * SECTOR_SIZE)) return RES_ERROR;
  return RES_OK;
}
static DRESULT rd_ioctl(BYTE, BYTE cmd, void *buff) {
  switch (cmd) {
    case CTRL_SYNC: return RES_OK;
    case GET_SECTOR_COUNT: *(LBA_t *)buff = SECTOR_COUNT; return RES_OK;
    case GET_SECTOR_SIZE: *(WORD *)buff = SECTOR_SIZE; return RES_OK;
    case GET_BLOCK_SIZE: *(DWORD *)buff = 1; return RES_OK;
  }
  return RES_PARERR;
}
static const ff_diskio_impl_t RD_IMPL = {rd_init, rd_status, rd_read, rd_write, rd_ioctl};

// ------------------------------------------------------------ USB 隨身碟回呼（在 TinyUSB 工作執行緒）
static uint8_t secTmp[SECTOR_SIZE];
static int32_t onRead(uint32_t lba, uint32_t offset, void *buffer, uint32_t bufsize) {
  uint64_t pos = (uint64_t)lba * SECTOR_SIZE + offset;
  if (pos + bufsize > (uint64_t)SECTOR_COUNT * SECTOR_SIZE) return -1;
  uint8_t *out = (uint8_t *)buffer;
  uint32_t left = bufsize;
  while (left) {
    uint32_t s = pos / SECTOR_SIZE, o = pos % SECTOR_SIZE, n = min(left, (uint32_t)(SECTOR_SIZE - o));
    if (o == 0 && n == SECTOR_SIZE) secRead(s, out);
    else { secRead(s, secTmp); memcpy(out, secTmp + o, n); }
    out += n; pos += n; left -= n;
  }
  readCount++;
  return bufsize;
}
static int32_t onWrite(uint32_t lba, uint32_t offset, uint8_t *buffer, uint32_t bufsize) {
  uint64_t pos = (uint64_t)lba * SECTOR_SIZE + offset;
  if (pos + bufsize > (uint64_t)SECTOR_COUNT * SECTOR_SIZE) return -1;
  const uint8_t *in = buffer;
  uint32_t left = bufsize;
  while (left) {
    uint32_t s = pos / SECTOR_SIZE, o = pos % SECTOR_SIZE, n = min(left, (uint32_t)(SECTOR_SIZE - o));
    bool ok;
    if (o == 0 && n == SECTOR_SIZE) ok = secWrite(s, in);
    else { secRead(s, secTmp); memcpy(secTmp + o, in, n); ok = secWrite(s, secTmp); }
    if (!ok) return -1;                       // 空間用完
    in += n; pos += n; left -= n;
  }
  writeCount++;
  lastWriteMs = millis();
  hostWrote = true;
  return bufsize;
}
static bool onStartStop(uint8_t, bool start, bool load_eject) {
  if (load_eject && !start) { hostEjected = true; ejectCount++; }      // 主機「安全移除」
  return true;
}

// ------------------------------------------------------------ 模擬拔插
void detachDisk() {
  if (!attached) return;
  MSC.mediaPresent(false);
  tud_disconnect();
  attached = false;
  delay(DETACH_SETTLE_MS);
}
void attachDisk() {
  if (attached) return;
  MSC.mediaPresent(true);
  tud_connect();
  attached = true;
}

bool mountFs() { return f_mount(&fatfs, "0:", 1) == FR_OK; }
void unmountFs() { f_mount(nullptr, "0:", 0); }

// ------------------------------------------------------------ 檔案工具
bool readFile(const char *path, uint8_t **out, size_t *len) {
  FIL f;
  if (f_open(&f, path, FA_READ) != FR_OK) return false;
  size_t n = f_size(&f);
  uint8_t *b = (uint8_t *)ps_malloc(n + 1);
  UINT got = 0;
  if (!b || f_read(&f, b, n, &got) != FR_OK || got != n) { f_close(&f); free(b); return false; }
  f_close(&f);
  b[n] = 0;
  *out = b;
  *len = n;
  return true;
}
bool writeFile(const char *path, const uint8_t *data, size_t len) {
  FIL f;
  if (f_open(&f, path, FA_WRITE | FA_CREATE_ALWAYS) != FR_OK) return false;
  UINT w = 0;
  FRESULT r = f_write(&f, data, len, &w);
  f_close(&f);
  return r == FR_OK && w == len;
}
uint32_t fnv(const uint8_t *d, size_t n) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < n; i++) { h ^= d[i]; h *= 16777619u; }
  return h;
}
String jsonEscape(const String &s) {
  String o;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if ((uint8_t)c < 0x20) o += ' ';
    else o += c;
  }
  return o;
}
String urlEncode(const String &s) {
  String o;
  const char *hex = "0123456789ABCDEF";
  for (size_t i = 0; i < s.length(); i++) {
    uint8_t c = s[i];
    if (isalnum(c) || c == '-' || c == '_' || c == '.') o += (char)c;
    else { o += '%'; o += hex[c >> 4]; o += hex[c & 15]; }
  }
  return o;
}
String urlDecode(const String &s) {
  String o;
  for (size_t i = 0; i < s.length(); i++) {
    if (s[i] == '%' && i + 2 < s.length()) { o += (char)strtol(s.substring(i + 1, i + 3).c_str(), nullptr, 16); i += 2; }
    else o += s[i];
  }
  return o;
}
bool isExport(const char *name) {
  String n = String(name);
  n.toUpperCase();
  return (n.startsWith("GLG_") || n.startsWith("AGL_")) && n.endsWith(".TXT");
}

// ------------------------------------------------------------ 設定
void loadConfig() {
  prefs.begin("vusb", true);
  cfg.ssid = prefs.getString("ssid", "");
  cfg.pass = prefs.getString("pass", "");
  cfg.server = prefs.getString("server", "");
  cfg.port = prefs.getInt("port", 8796);
  cfg.token = prefs.getString("token", "");
  cfg.machineNo = prefs.getInt("mno", 1);
  cfg.name = prefs.getString("name", "虛擬U盤");
  prefs.end();
}
void saveConfig() {
  prefs.begin("vusb", false);
  prefs.putString("ssid", cfg.ssid);
  prefs.putString("pass", cfg.pass);
  prefs.putString("server", cfg.server);
  prefs.putInt("port", cfg.port);
  prefs.putString("token", cfg.token);
  prefs.putInt("mno", cfg.machineNo);
  prefs.putString("name", cfg.name);
  prefs.end();
}

static const char *PASS_HIDDEN = "（已儲存，要改才填）";

String settingsText() {
  String ip = WiFi.isConnected() ? WiFi.localIP().toString() : String("（未連線）");
  String s = "\xEF\xBB\xBF";            // UTF-8 BOM，記事本才不會亂碼
  s += "# 資料助秘刷卡考勤系統－虛擬 U 盤設定\r\n";
  s += "# 填好後存檔，約 5 秒內生效。等號右邊是你的設定；以 # 開頭的是說明。\r\n";
  s += "# 電腦位址、埠、配對碼請看考勤系統「虛擬 U 盤」頁。\r\n\r\n";
  s += "WiFi名稱=" + cfg.ssid + "\r\n";
  s += "WiFi密碼=" + String(cfg.pass.length() ? PASS_HIDDEN : "") + "\r\n";
  s += "電腦位址=" + cfg.server + "\r\n";
  s += "埠=" + String(cfg.port) + "\r\n";
  s += "配對碼=" + cfg.token + "\r\n";
  s += "機號=" + String(cfg.machineNo) + "\r\n";
  s += "名稱=" + cfg.name + "\r\n\r\n";
  s += "# ---- 目前狀態（唯讀）----\r\n";
  s += "# 韌體版本：" FW_VERSION "\r\n";
  s += "# Wi-Fi：" + String(WiFi.isConnected() ? "已連線" : "未連線") + "　本機 IP：" + ip + "\r\n";
  s += "# 最近一次：" + lastStatus + "\r\n";
  return s;
}

// 讀「設定.txt」：有改就存起來，回傳 true＝設定變了
bool applySettingsFile() {
  uint8_t *b; size_t n;
  if (!readFile(SETTINGS_FILE, &b, &n)) return false;
  uint32_t h = fnv(b, n);
  if (h == settingsHash) { free(b); return false; }
  String t = String((char *)b);
  free(b);
  Config c = cfg;
  int pos = 0;
  while (pos < (int)t.length()) {
    int e = t.indexOf('\n', pos);
    if (e < 0) e = t.length();
    String line = t.substring(pos, e);
    pos = e + 1;
    line.trim();
    if (line.startsWith("\xEF\xBB\xBF")) line = line.substring(3);
    if (!line.length() || line[0] == '#') continue;
    int eq = line.indexOf('=');
    if (eq < 0) continue;
    String k = line.substring(0, eq), v = line.substring(eq + 1);
    k.trim(); v.trim();
    if (k == "WiFi名稱") c.ssid = v;
    else if (k == "WiFi密碼") { if (v.length() && v != PASS_HIDDEN) c.pass = v; else if (!v.length()) c.pass = ""; }
    else if (k == "電腦位址") c.server = v;
    else if (k == "埠") c.port = v.toInt() > 0 ? v.toInt() : 8796;
    else if (k == "配對碼") { v.toUpperCase(); c.token = v; }
    else if (k == "機號") c.machineNo = v.toInt() > 0 ? v.toInt() : 1;
    else if (k == "名稱") c.name = v;
  }
  bool changed = c.ssid != cfg.ssid || c.pass != cfg.pass || c.server != cfg.server || c.port != cfg.port ||
                 c.token != cfg.token || c.machineNo != cfg.machineNo || c.name != cfg.name;
  if (changed) {
    bool wifiChanged = c.ssid != cfg.ssid || c.pass != cfg.pass;
    cfg = c;
    saveConfig();
    Serial.println("[設定] 已更新");
    if (wifiChanged) { WiFi.disconnect(); WiFi.begin(cfg.ssid.c_str(), cfg.pass.c_str()); }
  }
  return changed;
}

void writeSettingsFile() {
  String s = settingsText();
  writeFile(SETTINGS_FILE, (const uint8_t *)s.c_str(), s.length());
  uint8_t *b; size_t n;
  if (readFile(SETTINGS_FILE, &b, &n)) { settingsHash = fnv(b, n); free(b); }
}

// ------------------------------------------------------------ 無線更新（韌體由考勤系統提供）
static String otaVersion;                 // 系統上有較新的韌體

bool versionNewer(const String &a, const String &b) {
  int x[3] = {0, 0, 0}, y[3] = {0, 0, 0};
  sscanf(a.c_str(), "%d.%d.%d", &x[0], &x[1], &x[2]);
  sscanf(b.c_str(), "%d.%d.%d", &y[0], &y[1], &y[2]);
  for (int i = 0; i < 3; i++) if (x[i] != y[i]) return x[i] > y[i];
  return false;
}
String baseUrl();
void doOta() {
  String v = otaVersion;
  otaVersion = "";
  Serial.printf("[更新] 下載韌體 %s\n", v.c_str());
  led(40, 0, 40);
  HTTPClient http;
  http.begin(baseUrl() + "/api/vusb/firmware");
  http.setTimeout(30000);
  http.addHeader("X-Token", cfg.token);
  int code = http.GET();
  int len = http.getSize();
  if (code != 200 || len <= 0 || !Update.begin(len)) { http.end(); lastStatus = "韌體更新失敗（下載）"; return; }
  size_t w = Update.writeStream(*http.getStreamPtr());
  http.end();
  if (w != (size_t)len || !Update.end(true)) { lastStatus = "韌體更新失敗（寫入）"; Update.abort(); return; }
  Serial.println("[更新] 完成，重新開機");
  delay(300);
  ESP.restart();
}

// ------------------------------------------------------------ 設定網頁（板子一直插在刷卡機上，不必拿下來）
// * 連上 Wi-Fi 後：同一個網路開 http://板子IP/（修改設定要輸入目前的配對碼）
// * 沒有 Wi-Fi 設定、或開機 60 秒還連不上：開熱點 ZMS-VUSB-xxxx（密碼 zmsvusb1），連上後開 http://192.168.4.1
// * Wi-Fi 名稱可從掃描清單選；存檔後同步寫回 U 盤裡的「設定.txt」
static WebServer web(80);
static DNSServer dns;
static bool portalOn = false;             // 熱點開著
static bool webStarted = false;
static uint32_t staConnectedAt = 0;
static String scanJson = "[]";
static uint32_t scanAt = 0;

String htmlEsc(const String &s) {
  String o;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '<') o += "&lt;";
    else if (c == '>') o += "&gt;";
    else if (c == '"') o += "&quot;";
    else if (c == '\'') o += "&#39;";
    else if (c == '&') o += "&amp;";
    else o += c;
  }
  return o;
}
bool viaHotspot() { return portalOn && web.client().localIP() == WiFi.softAPIP(); }

String statusJson() {
  String ip = WiFi.isConnected() ? WiFi.localIP().toString() : String("");
  return "{\"version\":\"" FW_VERSION "\",\"wifi\":" + String(WiFi.isConnected() ? "true" : "false") + ",\"ssid\":\"" +
         jsonEscape(WiFi.isConnected() ? WiFi.SSID() : String("")) + "\",\"ip\":\"" + ip + "\",\"rssi\":" +
         String(WiFi.isConnected() ? WiFi.RSSI() : 0) + ",\"host_mounted\":" + String(tud_mounted() ? "true" : "false") +
         ",\"reads\":" + String(readCount) + ",\"writes\":" + String(writeCount) + ",\"free\":" + String(freeBytes) +
         ",\"total\":" + String(totalBytes) + ",\"files\":[" + cachedFiles + "],\"last\":\"" + jsonEscape(lastStatus) +
         "\",\"server_ok\":" + String(lastStatus.startsWith("連不到") || lastStatus == "配對碼不正確" ? "false" : "true") + "}";
}

static const char PAGE_HEAD[] PROGMEM = R"HTML(<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>虛擬 U 盤設定</title><style>
body{font-family:"Microsoft JhengHei",sans-serif;max-width:520px;margin:16px auto;padding:0 14px;color:#1F2937;background:#F2F5FA}
h2{margin:6px 0 2px}.card{background:#fff;border:1px solid #E5E9F0;border-radius:14px;padding:14px;margin-top:12px}
label{display:block;margin-top:12px;font-weight:600;font-size:14px}
input,select{width:100%;padding:10px;font-size:16px;box-sizing:border-box;border:1px solid #CBD5E1;border-radius:9px;background:#FBFCFE}
.row{display:flex;gap:8px}.row>*{flex:1}.muted{color:#6B7280;font-size:13px}
button{margin-top:16px;width:100%;padding:12px;font-size:17px;background:#1E4D8C;color:#fff;border:0;border-radius:10px}
button.small{width:auto;margin:0;padding:9px 12px;font-size:14px;background:#0FA294}
.ok{color:#16A34A}.bad{color:#DC2626}.kv{display:grid;grid-template-columns:110px 1fr;gap:4px 8px;font-size:14px}
</style></head><body><h2>資料助秘刷卡考勤系統</h2><div class="muted">虛擬 U 盤設定（ESP32-S3）</div>
<div class="card"><b>目前狀態</b><div class="kv" id="st" style="margin-top:8px">讀取中…</div></div>
<form class="card" method="post" action="/save" accept-charset="utf-8"><b>設定</b>
<label>Wi-Fi 名稱（2.4GHz）</label><div class="row"><select id="ssidSel" onchange="pick()"><option>掃描中…</option></select>
<button type="button" class="small" onclick="scan(true)">重新掃描</button></div>
<input id="ssid" name="ssid" placeholder="或直接輸入 Wi-Fi 名稱" style="margin-top:6px" value=")HTML";

static const char PAGE_SCRIPT[] PROGMEM = R"HTML(<script>
function bars(r){return r>=-55?'▂▄▆█':r>=-67?'▂▄▆':r>=-75?'▂▄':'▂'}
function pick(){var s=document.getElementById('ssidSel');if(s.value)document.getElementById('ssid').value=s.value}
function scan(force){fetch('/scan'+(force?'?force=1':'')).then(r=>r.json()).then(d=>{var s=document.getElementById('ssidSel');
if(d.scanning){s.innerHTML='<option value="">掃描中…</option>';setTimeout(scan,1500);return}
var cur=document.getElementById('ssid').value;s.innerHTML='<option value="">— 選擇附近的 Wi-Fi —</option>';
d.list.forEach(n=>{var o=document.createElement('option');o.value=n.ssid;o.textContent=n.ssid+'　'+bars(n.rssi)+(n.open?'（開放）':'');
if(n.ssid==cur)o.selected=true;s.appendChild(o)})})}
function mb(b){return (b/1048576).toFixed(1)+' MB'}
function status(){fetch('/status').then(r=>r.json()).then(d=>{document.getElementById('st').innerHTML=
'<span>韌體</span><span>'+d.version+'</span><span>Wi-Fi</span><span class="'+(d.wifi?'ok':'bad')+'">'+(d.wifi?('已連線 '+d.ssid+'（'+d.ip+'，'+d.rssi+' dBm）'):'未連線')+'</span>'+
'<span>考勤機</span><span class="'+(d.host_mounted?'ok':'bad')+'">'+(d.host_mounted?'已掛上 U 盤':'沒有掛上 U 盤')+'（讀 '+d.reads+'、寫 '+d.writes+'）</span>'+
'<span>U 盤空間</span><span>剩 '+mb(d.free)+'／共 '+mb(d.total)+'</span>'+
'<span>U 盤檔案</span><span>'+(d.files.length?d.files.join('、'):'（只有設定）')+'</span>'+
'<span>最近一次</span><span>'+d.last+'</span>'})}
scan(false);status();setInterval(status,5000);
</script></body></html>)HTML";

void portalPage() {
  String h = FPSTR(PAGE_HEAD);
  h += htmlEsc(cfg.ssid) + "\">";
  h += "<label>Wi-Fi 密碼（不改請留空）</label><input name=\"pass\" type=\"password\" autocomplete=\"new-password\">";
  h += "<label>電腦位址（考勤系統「虛擬 U 盤」頁）</label><input name=\"server\" value=\"" + htmlEsc(cfg.server) + "\">";
  h += "<div class=\"row\"><div><label>埠</label><input name=\"port\" value=\"" + String(cfg.port) + "\"></div>";
  h += "<div><label>機號</label><input name=\"mno\" value=\"" + String(cfg.machineNo) + "\"></div></div>";
  h += "<label>配對碼</label><input name=\"token\" value=\"" + htmlEsc(viaHotspot() ? cfg.token : String("")) +
       "\" placeholder=\"" + String(viaHotspot() ? "" : "不改請留空") + "\">";
  h += "<label>名稱</label><input name=\"name\" value=\"" + htmlEsc(cfg.name) + "\">";
  if (!viaHotspot() && cfg.token.length())
    h += "<label>目前的配對碼（確認身分用）</label><input name=\"auth\" placeholder=\"考勤系統「虛擬 U 盤」頁的配對碼\">";
  h += "<button>儲存（同時寫入 U 盤的 設定.txt）</button></form>";
  h += FPSTR(PAGE_SCRIPT);
  web.send(200, "text/html; charset=utf-8", h);
}

void portalScan() {
  int n = WiFi.scanComplete();
  if (web.hasArg("force") || (n == WIFI_SCAN_FAILED && millis() - scanAt > 3000) || (scanJson == "[]" && n == WIFI_SCAN_FAILED)) {
    WiFi.scanNetworks(true);
    scanAt = millis();
    web.send(200, "application/json", "{\"scanning\":true}");
    return;
  }
  if (n == WIFI_SCAN_RUNNING) { web.send(200, "application/json", "{\"scanning\":true}"); return; }
  if (n >= 0) {
    std::vector<String> seen;
    String j = "[";
    for (int i = 0; i < n; i++) {
      String s = WiFi.SSID(i);
      if (!s.length()) continue;
      bool dup = false;
      for (auto &x : seen) if (x == s) { dup = true; break; }
      if (dup) continue;
      seen.push_back(s);
      if (j.length() > 1) j += ",";
      j += "{\"ssid\":\"" + jsonEscape(s) + "\",\"rssi\":" + String(WiFi.RSSI(i)) + ",\"open\":" +
           String(WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "true" : "false") + "}";
    }
    scanJson = j + "]";
    WiFi.scanDelete();
  }
  web.send(200, "application/json; charset=utf-8", "{\"list\":" + scanJson + "}");
}

void portalStatus() { web.send(200, "application/json; charset=utf-8", statusJson()); }

void portalSave() {
  if (!viaHotspot() && cfg.token.length() && web.arg("auth") != cfg.token) {
    String a = web.arg("auth");
    a.toUpperCase();
    if (a != cfg.token) {
      web.send(403, "text/html; charset=utf-8",
               "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
               "<h3>配對碼不正確，設定沒有變更。</h3><a href='/'>回設定頁</a>");
      return;
    }
  }
  String oldSsid = cfg.ssid, oldPass = cfg.pass;
  if (web.arg("ssid").length()) cfg.ssid = web.arg("ssid");
  if (web.arg("pass").length()) cfg.pass = web.arg("pass");
  if (web.hasArg("server")) { cfg.server = web.arg("server"); cfg.server.trim(); }
  if (web.arg("port").toInt() > 0) cfg.port = web.arg("port").toInt();
  if (web.arg("token").length()) { cfg.token = web.arg("token"); cfg.token.trim(); cfg.token.toUpperCase(); }
  if (web.arg("mno").toInt() > 0) cfg.machineNo = web.arg("mno").toInt();
  if (web.arg("name").length()) cfg.name = web.arg("name");
  saveConfig();
  wantSync = true;                           // 同步把 設定.txt 寫回 U 盤
  lastHello = 0;
  web.send(200, "text/html; charset=utf-8",
           "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
           "<h3>已儲存，並寫入 U 盤的 設定.txt。</h3><p>約 20 秒後，考勤系統「虛擬 U 盤」頁會更新。</p><a href='/'>回設定頁</a>");
  if (cfg.ssid != oldSsid || cfg.pass != oldPass) {
    delay(300);
    WiFi.disconnect();
    WiFi.begin(cfg.ssid.c_str(), cfg.pass.c_str());
  }
}

void startWeb() {
  if (webStarted) return;
  web.on("/", portalPage);
  web.on("/scan", portalScan);
  web.on("/status", portalStatus);
  web.on("/save", HTTP_POST, portalSave);
  web.onNotFound(portalPage);
  web.begin();
  webStarted = true;
}
void startPortal() {
  if (portalOn) return;
  String mac = WiFi.macAddress();
  mac.replace(":", "");
  String ssid = "ZMS-VUSB-" + mac.substring(8);
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(ssid.c_str(), "zmsvusb1");
  dns.start(53, "*", WiFi.softAPIP());
  startWeb();
  portalOn = true;
  Serial.printf("[熱點] %s 密碼 zmsvusb1，http://192.168.4.1\n", ssid.c_str());
}
void stopPortal() {
  if (!portalOn) return;
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  portalOn = false;
  Serial.println("[熱點] 關閉");
}

// ------------------------------------------------------------ 考勤系統通訊
String baseUrl() { return "http://" + cfg.server + ":" + String(cfg.port); }
bool serverReady() { return WiFi.isConnected() && cfg.server.length() && cfg.token.length(); }

// 回傳待送檔案的 id 清單；失敗回傳 false
bool hello(std::vector<int> &pending) {
  if (!serverReady()) return false;
  HTTPClient http;
  http.begin(baseUrl() + "/api/vusb/hello");
  http.setTimeout(8000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Token", cfg.token);
  String body = "{\"agent\":\"" + jsonEscape(WiFi.macAddress()) + "\",\"name\":\"" + jsonEscape(cfg.name) +
                "\",\"machine_no\":" + String(cfg.machineNo) + ",\"version\":\"" FW_VERSION "\",\"free_bytes\":" +
                String(freeBytes) + ",\"total_bytes\":" + String(totalBytes) + ",\"files\":[" + cachedFiles +
                "],\"state\":\"" + String(attached ? "attached" : "busy") + "\",\"host_mounted\":" +
                String(tud_mounted() ? "true" : "false") + ",\"mounts\":" + String(mountCount) + ",\"reads\":" +
                String(readCount) + ",\"writes\":" + String(writeCount) + ",\"ejects\":" + String(ejectCount) +
                ",\"pool_full\":" + String(poolFull ? "true" : "false") + ",\"rssi\":" + String(WiFi.RSSI()) +
                ",\"uptime\":" + String(millis() / 1000) + "}";
  int code = http.POST(body);
  if (code != 200) {
    lastStatus = code == 401 ? "配對碼不正確" : "連不到考勤系統（" + String(code) + "）";
    http.end();
    return false;
  }
  String r = http.getString();
  http.end();
  // 系統時間（讓檔案日期正確）："time": "2026-11-17T13:51:38"
  int tp = r.indexOf("\"time\": \"");
  if (tp < 0) tp = r.indexOf("\"time\":\"");
  if (tp >= 0) {
    int q = r.indexOf('"', r.indexOf(':', tp) + 1);
    struct tm tm = {};
    if (sscanf(r.c_str() + q + 1, "%d-%d-%dT%d:%d:%d", &tm.tm_year, &tm.tm_mon, &tm.tm_mday, &tm.tm_hour, &tm.tm_min,
               &tm.tm_sec) == 6) {
      tm.tm_year -= 1900; tm.tm_mon -= 1;
      setenv("TZ", "UTC", 1); tzset();
      time_t t = mktime(&tm);
      struct timeval tv = {t, 0};
      settimeofday(&tv, nullptr);
    }
  }
  int fv = r.indexOf("\"firmware_version\"");
  if (fv >= 0) {
    int q1 = r.indexOf('"', r.indexOf(':', fv) + 1), q2 = r.indexOf('"', q1 + 1);
    String v = r.substring(q1 + 1, q2);
    Serial.printf("[更新] 系統提供韌體 %s（目前 " FW_VERSION "）\n", v.c_str());
    if (versionNewer(v, FW_VERSION)) otaVersion = v;
  }
  int p = r.indexOf("\"pending\"");
  while (p >= 0) {
    p = r.indexOf("\"id\"", p);
    if (p < 0) break;
    p = r.indexOf(':', p) + 1;
    pending.push_back(r.substring(p).toInt());
  }
  return true;
}

// 上傳一個檔案；回傳 true＝系統已收下（含重複），可以刪除
bool uploadFile(const char *name, const uint8_t *data, size_t len, String &msg) {
  unsigned char sha[20];
  mbedtls_sha1(data, len, sha);
  char hex[41];
  for (int i = 0; i < 20; i++) sprintf(hex + i * 2, "%02x", sha[i]);
  HTTPClient http;
  http.begin(baseUrl() + "/api/vusb/upload?agent=" + urlEncode(WiFi.macAddress()) + "&machine_no=" +
             String(cfg.machineNo) + "&name=" + urlEncode(name));
  http.setTimeout(30000);
  http.addHeader("Content-Type", "application/octet-stream");
  http.addHeader("X-Token", cfg.token);
  http.addHeader("X-SHA1", hex);
  int code = http.POST((uint8_t *)data, len);
  String r = http.getString();
  http.end();
  bool ok = code == 200 && r.indexOf("\"ok\": true") >= 0;
  int m = r.indexOf("\"message\"");
  msg = m >= 0 ? r.substring(r.indexOf('"', r.indexOf(':', m) + 1) + 1, r.indexOf('"', r.indexOf('"', r.indexOf(':', m) + 1) + 1))
               : String(code);
  return ok;
}

// 取回一個待送檔案並寫進 U 盤
bool fetchOutbox(int id) {
  HTTPClient http;
  http.begin(baseUrl() + "/api/vusb/outbox/" + String(id));
  http.setTimeout(30000);
  http.addHeader("X-Token", cfg.token);
  const char *keys[] = {"X-Filename"};
  http.collectHeaders(keys, 1);
  int code = http.GET();
  if (code != 200) { http.end(); return false; }
  String name = urlDecode(http.header("X-Filename"));
  int len = http.getSize();
  if (!name.length() || len <= 0) { http.end(); return false; }
  uint8_t *buf = (uint8_t *)ps_malloc(len);
  WiFiClient *st = http.getStreamPtr();
  int got = 0;
  uint32_t t0 = millis();
  while (got < len && millis() - t0 < 20000) {
    int a = st->available();
    if (a > 0) got += st->readBytes(buf + got, min(a, len - got));
    else delay(5);
  }
  http.end();
  bool ok = got == len && writeFile(("0:/" + name).c_str(), buf, len);
  free(buf);
  if (!ok) return false;
  HTTPClient d;
  d.begin(baseUrl() + "/api/vusb/outbox/" + String(id) + "/done?agent=" + urlEncode(WiFi.macAddress()));
  d.addHeader("X-Token", cfg.token);
  d.POST((uint8_t *)"", 0);
  d.end();
  placed.push_back(name);
  Serial.printf("[取回] %s %d bytes\n", name.c_str(), len);
  lastStatus = "已放入 " + name;
  return true;
}

// ------------------------------------------------------------ 同步（拔出 → 處理 → 插回）
bool skipName(const char *name, BYTE attr) {
  if (attr & (AM_HID | AM_SYS)) return true;
  String n = name;
  if (n == "設定.txt" || n.equalsIgnoreCase("System Volume Information")) return true;
  for (auto &p : placed) if (p == n) return true;
  return false;
}

// 要傳回系統的檔案（相對路徑；含第一層資料夾內的檔案，例如 EnrollPhoto/1.jpg）
void collectUploads(std::vector<String> &out) {
  FF_DIR dir;
  FILINFO fi;
  std::vector<String> dirs;
  if (f_opendir(&dir, "0:/") != FR_OK) return;
  while (f_readdir(&dir, &fi) == FR_OK && fi.fname[0]) {
    if (skipName(fi.fname, fi.fattrib)) continue;
    if (fi.fattrib & AM_DIR) dirs.push_back(fi.fname);
    else out.push_back(fi.fname);
  }
  f_closedir(&dir);
  for (auto &d : dirs) {
    if (f_opendir(&dir, ("0:/" + d).c_str()) != FR_OK) continue;
    while (f_readdir(&dir, &fi) == FR_OK && fi.fname[0])
      if (!(fi.fattrib & (AM_DIR | AM_HID | AM_SYS))) out.push_back(d + "/" + fi.fname);
    f_closedir(&dir);
  }
}

void refreshListing() {
  cachedFiles = "";
  hasExports = false;
  FF_DIR dir;
  FILINFO fi;
  if (f_opendir(&dir, "0:/") == FR_OK) {
    while (f_readdir(&dir, &fi) == FR_OK && fi.fname[0]) {
      if (fi.fattrib & (AM_DIR | AM_HID | AM_SYS)) continue;
      if (cachedFiles.length()) cachedFiles += ",";
      cachedFiles += "\"" + jsonEscape(fi.fname) + "\"";
    }
    f_closedir(&dir);
  }
  std::vector<String> up;
  collectUploads(up);
  hasExports = !up.empty();
  DWORD fre;
  FATFS *fs;
  if (f_getfree("0:", &fre, &fs) == FR_OK) {
    totalBytes = (fs->n_fatent - 2) * fs->csize * SECTOR_SIZE;
    freeBytes = fre * fs->csize * SECTOR_SIZE;
    if (freeBytes > poolFreeBytes()) freeBytes = poolFreeBytes();     // 實際能存的空間
    totalBytes = POOL_SECTORS * SECTOR_SIZE;
  }
}

void syncNow(const std::vector<int> &pending) {
  led(0, 0, 40);
  detachDisk();
  if (!mountFs()) {
    Serial.println("[同步] 檔案系統錯誤，重新格式化");
    formatDisk();
    mountFs();
  }
  bool cfgChanged = applySettingsFile();
  // 1. 上傳考勤機匯出的檔案
  if (serverReady()) {
    std::vector<String> names;
    collectUploads(names);
    for (auto &n : names) {
      uint8_t *b; size_t len;
      String path = "0:/" + n;
      if (!readFile(path.c_str(), &b, &len)) continue;
      String msg;
      bool ok = uploadFile(n.c_str(), b, len, msg);
      free(b);
      Serial.printf("[上傳] %s → %s %s\n", n.c_str(), ok ? "OK" : "失敗", msg.c_str());
      if (ok) {
        f_unlink(path.c_str());
        int slash = n.indexOf('/');
        if (slash > 0) f_unlink(("0:/" + n.substring(0, slash)).c_str());   // 資料夾空了才會刪得掉
        lastStatus = n + "：" + msg;
      }
      else lastStatus = n + " 上傳失敗：" + msg;
    }
    // 2. 放入待送的人員資訊表
    for (int id : pending) fetchOutbox(id);
  } else {
    lastStatus = WiFi.isConnected() ? "還沒設定電腦位址或配對碼" : "Wi-Fi 未連線";
  }
  writeSettingsFile();
  refreshListing();
  unmountFs();
  attachDisk();
  hostWrote = false;
  hostEjected = false;
  led(0, serverReady() ? 30 : 0, serverReady() ? 0 : 30);
  if (cfgChanged) lastHello = 0;          // 設定變了，馬上回報
}

void formatDisk() {
  diskReset();
  static uint8_t work[FF_MAX_SS];
  MKFS_PARM opt = {FM_FAT32, 2, 0, 0, SECTOR_SIZE};     // 和一般隨身碟一樣的 FAT32（兩份 FAT 表）
  FRESULT r = f_mkfs("0:", &opt, work, sizeof(work));
  if (r == FR_OK) {
    f_mount(&fatfs, "0:", 1);
    f_setlabel("0:VUSB");
    f_mount(nullptr, "0:", 0);
  }
  Serial.printf("[格式化] %d\n", r);
}

// ------------------------------------------------------------ 主程式
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n虛擬 U 盤 " FW_VERSION);
  led(30, 20, 0);
  secmap = (uint16_t *)ps_malloc(sizeof(uint16_t) * SECTOR_COUNT);
  pool = (uint8_t *)ps_malloc((size_t)POOL_SECTORS * SECTOR_SIZE);
  freeList = (uint16_t *)ps_malloc(sizeof(uint16_t) * POOL_SECTORS);
  if (!secmap || !pool || !freeList) { Serial.println("PSRAM 配置失敗"); led(40, 0, 0); while (true) delay(1000); }
  ff_diskio_register(0, &RD_IMPL);
  formatDisk();
  loadConfig();
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("vusb-attendance");
  if (cfg.ssid.length()) WiFi.begin(cfg.ssid.c_str(), cfg.pass.c_str());
  else startPortal();
  startWeb();
  // 開機先放好設定.txt
  mountFs();
  writeSettingsFile();
  refreshListing();
  unmountFs();
  MSC.vendorID("ZMS");
  MSC.productID("Attendance VUSB");
  MSC.productRevision("1.0");
  MSC.onRead(onRead);
  MSC.onWrite(onWrite);
  MSC.onStartStop(onStartStop);
  MSC.mediaPresent(true);
  MSC.isWritable(true);
  MSC.begin(SECTOR_COUNT, SECTOR_SIZE);
  USB.begin();
  attached = true;
  Serial.println("U 盤已就緒（FAT32 64MB，可存 6MB）");
}

void loop() {
  uint32_t now = millis();
  // 設定熱點：開機 60 秒還連不上 Wi-Fi 就開；連上 2 分鐘後關
  static bool wasMounted = false;
  bool hm = tud_mounted();
  if (hm && !wasMounted) mountCount++;
  wasMounted = hm;
  if (WiFi.isConnected()) {
    if (!staConnectedAt) staConnectedAt = now;
    if (portalOn && now - staConnectedAt > 120000) stopPortal();
  } else {
    staConnectedAt = 0;
    if (!portalOn && now > 60000) startPortal();
  }
  if (portalOn) dns.processNextRequest();
  web.handleClient();
  if (otaVersion.length() && WiFi.isConnected()) doOta();
  // 主機寫完（靜止 4 秒）或按了安全移除 → 同步
  if ((hostWrote && now - lastWriteMs > IDLE_AFTER_WRITE_MS) || hostEjected) wantSync = true;
  // 有還沒傳出去的檔案（上次 Wi-Fi 斷線）→ 每分鐘重試
  if (hasExports && serverReady() && now - lastRetry > RETRY_INTERVAL_MS) { lastRetry = now; wantSync = true; }
  std::vector<int> pending;
  if (now - lastHello > HELLO_INTERVAL_MS || lastHello == 0) {
    lastHello = now;
    if (hello(pending) && !pending.empty()) wantSync = true;
    else if (serverReady()) lastStatus = lastStatus.startsWith("連不到") ? "連線正常" : lastStatus;
  }
  if (wantSync) {
    wantSync = false;
    if (pending.empty() && serverReady()) { std::vector<int> p2; hello(p2); pending = p2; }
    syncNow(pending);
  }
  static uint32_t lastBlink = 0;
  if (now - lastBlink > 2000) {
    lastBlink = now;
    if (!WiFi.isConnected()) led(30, 0, 0);
  }
  static uint32_t lastDiag = 0;
  if (now - lastDiag > 5000) {
    lastDiag = now;
    Serial.printf("[狀態] USB 主機%s　Wi-Fi %s　剩餘 %u/%u\n", tud_mounted() ? "已連接" : "未連接",
                  WiFi.isConnected() ? WiFi.localIP().toString().c_str() : "未連線", freeBytes, totalBytes);
  }
  delay(50);
}
