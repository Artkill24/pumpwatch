# pumpwatch

Raccolta dati su nuovi token pump.fun: per ogni mint registra
concentrazione holder + stato della bonding curve a 30s / 2min / 10min.

Solo raccolta. Nessun segnale di acquisto: lo scoring si scrive dopo,
sui dati veri.

## Dipendenze
Tutte Python puro, nessuna compilazione: websockets, httpx, aiosqlite.

## Termux (Android)
    pkg update && pkg upgrade -y
    pkg install python git -y
    pip install -r requirements.txt
    termux-wake-lock
    export HELIUS_API_KEY=la_tua_chiave
    python main.py

Disattiva l'ottimizzazione batteria per Termux nelle impostazioni Android,
altrimenti Android uccide il processo dopo poche ore.

## VPS (per accumulare davvero)
    sudo apt update && sudo apt install -y python3 python3-pip python3-venv
    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    export HELIUS_API_KEY=la_tua_chiave
    nohup python main.py > pumpwatch.log 2>&1 &

Oppure come servizio systemd (vedi sotto).

## Report
    python report.py

## systemd
    [Unit]
    Description=pumpwatch
    After=network-online.target

    [Service]
    WorkingDirectory=/opt/pumpwatch
    Environment=HELIUS_API_KEY=la_tua_chiave
    ExecStart=/opt/pumpwatch/venv/bin/python main.py
    Restart=always
    RestartSec=10
    User=pumpwatch

    [Install]
    WantedBy=multi-user.target
