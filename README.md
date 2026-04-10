# Marzban Lemon Squeezy Webhook Server

FastAPI server that listens for Lemon Squeezy webhook events and automatically creates/manages Marzban users.

## Installation

```bash
cp -r . /opt/marzban-webhook
cd /opt/marzban-webhook
pip3 install -r requirements.txt
cp .env.example .env
nano .env
```

## Running as a service

```bash
cp marzban-webhook.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now marzban-webhook
systemctl status marzban-webhook
```

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

## Lemon Squeezy Setup

**Products**
- Create a monthly and yearly subscription product.
- Copy each Variant ID into `.env`.

**Webhook**
- Go to Settings > Webhooks > Add webhook.
- URL: `https://webhook.yourdomain.com/webhook/lemonsqueezy`
- Events: `subscription_created`, `subscription_updated`, `subscription_expired`, `subscription_cancelled`
- Copy the signing secret into `.env` as `LEMONSQUEEZY_WEBHOOK_SECRET`.

**Confirmation page**
- After payment, redirect to your thank-you page and call:
  `GET https://webhook.yourdomain.com/subscription/{username}`
- Use the returned `subscription_url` to display the Marzban link.

## Environment Variables

| Variable | Description |
|----------|-------------|
| LEMONSQUEEZY_WEBHOOK_SECRET | Webhook signing secret from Lemon Squeezy |
| MARZBAN_URL | Marzban panel URL (e.g. http://localhost:8000) |
| MARZBAN_USERNAME | Marzban admin username |
| MARZBAN_PASSWORD | Marzban admin password |
| PLAN_MONTHLY_VARIANT_ID | Lemon Squeezy monthly plan variant ID |
| PLAN_YEARLY_VARIANT_ID | Lemon Squeezy yearly plan variant ID |

## User Rules

- Username is generated from the customer email plus a 6-digit random suffix.
- Data limit: 400 GB per month, resets monthly.
- Monthly plan: 31 days. Yearly plan: 366 days.
