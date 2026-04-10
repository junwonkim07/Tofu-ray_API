#!/bin/bash
set -e

INSTALL_DIR="/opt/marzban-webhook"
SERVICE_NAME="marzban-webhook"

echo "=== Marzban Webhook Server Installer ==="
echo ""

# 필수 패키지 확인
if ! command -v pip3 &>/dev/null; then
    echo "[*] Installing python3-pip..."
    apt-get install -y python3-pip
fi

# 설치 디렉토리 생성
echo "[*] Copying files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp main.py requirements.txt marzban-webhook.service "$INSTALL_DIR/"

# pip 패키지 설치
echo "[*] Installing Python packages..."
pip3 install -q -r "$INSTALL_DIR/requirements.txt"

# .env 설정
echo ""
echo "=== Configuration ==="

read -p "Marzban URL (default: http://localhost:8000): " MARZBAN_URL
MARZBAN_URL="${MARZBAN_URL:-http://localhost:8000}"

read -p "Marzban admin username: " MARZBAN_USERNAME
read -s -p "Marzban admin password: " MARZBAN_PASSWORD
echo ""

read -p "Lemon Squeezy webhook secret: " LS_SECRET
read -p "Monthly plan Variant ID: " MONTHLY_ID
read -p "Yearly plan Variant ID: " YEARLY_ID

cat > "$INSTALL_DIR/.env" <<EOF
LEMONSQUEEZY_WEBHOOK_SECRET=$LS_SECRET
MARZBAN_URL=$MARZBAN_URL
MARZBAN_USERNAME=$MARZBAN_USERNAME
MARZBAN_PASSWORD=$MARZBAN_PASSWORD
PLAN_MONTHLY_VARIANT_ID=$MONTHLY_ID
PLAN_YEARLY_VARIANT_ID=$YEARLY_ID
EOF

chmod 600 "$INSTALL_DIR/.env"
echo "[*] .env saved."

# systemd 서비스 등록
echo "[*] Registering systemd service..."
cp "$INSTALL_DIR/marzban-webhook.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo ""
echo "=== Done ==="
systemctl status "$SERVICE_NAME" --no-pager
echo ""
echo "Webhook endpoint: http://YOUR_SERVER_IP:8001/webhook/lemonsqueezy"
