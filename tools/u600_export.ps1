param([string]$Db = "D:\U600_FT\tmkq.mdb", [string]$Out = "u600.json", [string]$Password = "Timmy")
# Must run in 32-bit PowerShell (Jet 4.0 is 32-bit only). Read-only: copies the mdb first.
# Exports U600 departments, employees (card_id / card_sn), enrolled device users and attendance records as JSON.
$ErrorActionPreference = "Stop"
$tmp = Join-Path $env:TEMP ("u600_" + [guid]::NewGuid().ToString("N") + ".mdb")
Copy-Item -LiteralPath $Db -Destination $tmp
try {
  $c = New-Object System.Data.OleDb.OleDbConnection("Provider=Microsoft.Jet.OLEDB.4.0;Data Source=$tmp;Jet OLEDB:Database Password=$Password")
  $c.Open()
  function Q([string]$sql) {
    $a = New-Object System.Data.OleDb.OleDbDataAdapter($sql, $c)
    $t = New-Object System.Data.DataTable
    try { [void]$a.Fill($t) } catch { return @() }
    $rows = @()
    foreach ($r in $t.Rows) {
      $h = [ordered]@{}
      foreach ($col in $t.Columns) {
        $v = $r[$col.ColumnName]
        if ($v -is [DBNull]) { $v = $null }
        elseif ($v -is [DateTime]) { $v = $v.ToString("yyyy-MM-dd HH:mm:ss") }
        elseif ($v -is [byte[]]) { $v = $null }
        elseif ($v -is [string]) { $v = $v.Trim() }
        $h[$col.ColumnName] = $v
      }
      $rows += [pscustomobject]$h
    }
    return ,$rows
  }
  $tables = @($c.GetSchema("Tables") | Where-Object { $_.TABLE_TYPE -eq "TABLE" } | ForEach-Object { $_.TABLE_NAME })
  $res = [ordered]@{
    source = $Db
    exported = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    tables = $tables
    depart = Q "SELECT * FROM Depart"
    employee = Q "SELECT emp_id, card_id, card_sn, emp_name, depart_id, sex, hire_date, end_date, id_card, phone_code, email, no_sign FROM Employee"
    leaved = Q "SELECT emp_id, card_id, card_sn, emp_name, depart_id, hire_date, end_date FROM Leaved_Emps"
    enroll_dface = Q "SELECT EMachineNumber, EnrollNumber, UserName, FingerNumber, Privilege, Password FROM tblzEnrollDFace"
    enroll_face = Q "SELECT EMachineNumber, EnrollNumber, UserName, FingerNumber, Privilege, Password FROM tblzEnrollFace"
    enroll = Q "SELECT EMachineNumber, EnrollNumber, UserName, FingerNumber, Privilege, Password FROM tblzEnroll"
    clocks = Q "SELECT * FROM ClockDFace"
    records = Q "SELECT clock_id, card_id, emp_id, KqDate, KqTime, mark, flag, sign_cause FROM tmpTRecords"
    records1 = Q "SELECT * FROM TimeRecords1"
  }
  $c.Close()
  $json = $res | ConvertTo-Json -Depth 5
  [IO.File]::WriteAllText($Out, $json, (New-Object Text.UTF8Encoding($false)))
} finally {
  Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
}
