# Marzban Lemon Squeezy Webhook Server

Automatically creates and manages Marzban users when a Lemon Squeezy subscription is purchased.

## Requirements

- Linux server with Marzban already running
- Python 3.8+
- Root access

## Installation

```bash
git clone https://github.com/junwonkim07/Tofu-ray_API.git
cd marzban-webhook
bash install.sh
```

The script will prompt for the following and set everything up automatically:

- Marzban URL, admin username, and password
- Lemon Squeezy webhook secret
- Monthly and yearly plan Variant IDs

## Lemon Squeezy Setup

**Webhook**

- Settings > Webhooks > Add webhook
- URL: `https://webhook.yourdomain.com/webhook/lemonsqueezy`
- Events: `subscription_created`, `subscription_updated`, `subscription_expired`, `subscription_cancelled`

**Confirmation page**

- After payment, call `GET /subscription/{username}` to retrieve the Marzban subscription link and display it to the user.

## Nginx

```nginx
server {
    listen 443 ssl;
    server_name webhook.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Service Management

```bash
systemctl status marzban-webhook
systemctl restart marzban-webhook
journalctl -u marzban-webhook -f
```
