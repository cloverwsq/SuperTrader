$ErrorActionPreference = 'Stop'

Set-Location 'C:\Users\35882\Documents\freqtrade'

$monitorLog = 'user_data/logs/paper_monitor.log'
"=== monitor start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $monitorLog -Encoding utf8 -Append

for ($i = 0; $i -lt 120; $i++) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $tail = Get-Content 'user_data/logs/freqtrade.log' -Tail 25 -ErrorAction SilentlyContinue
    $matches = @(
        $tail | Where-Object {
            $_ -match 'Entering|enter_long|enter_short|exit_long|exit_short|order|filled|cancell|RequestTimeout|ExchangeNotAvailable|Could not load markets|heartbeat|Outdated history'
        }
    )

    $db = @'
import sqlite3
from pathlib import Path

path = Path("user_data/tradesv3.sqlite")
if not path.exists():
    print("trade_count=DB_MISSING")
    raise SystemExit(0)

con = sqlite3.connect(path)
cur = con.cursor()
for label, sql in [
    ("trade_count", "SELECT COUNT(*) FROM trades"),
    ("open_trade_count", "SELECT COUNT(*) FROM trades WHERE is_open = 1"),
    ("order_count", "SELECT COUNT(*) FROM orders"),
]:
    cur.execute(sql)
    print(f"{label}={cur.fetchone()[0]}")
con.close()
'@ | python -

    Add-Content -Path $monitorLog -Value "[$stamp]"
    $db | ForEach-Object { Add-Content -Path $monitorLog -Value $_ }

    if ($matches.Count -gt 0) {
        Add-Content -Path $monitorLog -Value 'recent_log_matches:'
        $matches | ForEach-Object { Add-Content -Path $monitorLog -Value $_ }
    }
    else {
        Add-Content -Path $monitorLog -Value 'recent_log_matches:none'
    }

    Add-Content -Path $monitorLog -Value ''
    Start-Sleep -Seconds 30
}
