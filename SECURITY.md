# Security Guide

This document covers the security configuration required to run the SoftLedger API Middleware safely in a production environment.

Finance infrastructure is a high-value target. Every component of this deployment should be treated as security-critical.

---

## Architecture Overview

```
Internet
    |
    v
[Firewall / WAF]
    |
    v
[Nginx Reverse Proxy] — SSL termination, rate limiting, IP filtering
    |
    v
[Middleware API] — API key auth, input validation, audit logging
    |
    v
[SoftLedger API] — authenticated via OAuth2 client credentials
```

---

## 1. Server Hardening

### Operating System

Use a dedicated server or VPS running Ubuntu 22.04 LTS or later.

Keep the system updated:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

### Create a dedicated service user

Never run the middleware as root:
```bash
sudo useradd --system --shell /bin/false --home /opt/sl-middleware sl-middleware
sudo mkdir -p /opt/sl-middleware
sudo chown sl-middleware:sl-middleware /opt/sl-middleware
```

### Disable root SSH login

Edit `/etc/ssh/sshd_config`:
```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

---

## 2. Firewall Configuration

Use UFW (Uncomplicated Firewall) to restrict inbound traffic:

```bash
# Reset to defaults
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH from your office IP only
sudo ufw allow from YOUR_OFFICE_IP to any port 22

# Allow HTTPS from anywhere (if public-facing)
sudo ufw allow 443/tcp

# Allow HTTP only to redirect to HTTPS
sudo ufw allow 80/tcp

# Enable the firewall
sudo ufw enable
sudo ufw status verbose
```

If your middleware only needs to receive traffic from known sources (specific servers, office IPs):

```bash
# Allow only from known source IPs
sudo ufw allow from KNOWN_SOURCE_IP to any port 443
```

### Additional firewall rules for crypto/financial APIs

Block known malicious IP ranges using threat intelligence feeds:
```bash
# Install and configure fail2ban
sudo apt install fail2ban
sudo systemctl enable fail2ban
```

Create `/etc/fail2ban/jail.local`:
```ini
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[nginx-http-auth]
enabled = true

[nginx-limit-req]
enabled = true
```

---

## 3. Nginx Reverse Proxy

Install Nginx and configure it as a reverse proxy with SSL:

```bash
sudo apt install nginx certbot python3-certbot-nginx
```

Create `/etc/nginx/sites-available/sl-middleware`:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL certificate (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Strong SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "no-referrer" always;

    # Rate limiting at Nginx level (first line of defence)
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
    limit_req zone=api burst=10 nodelay;

    # Request size limit
    client_max_body_size 1M;

    # Proxy to middleware
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 10s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # Health check — allow without rate limiting
    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        limit_req off;
    }

    # Block common attack paths
    location ~ /\. { deny all; }
    location ~ /\.env { deny all; }
    location ~ /\.git { deny all; }
}
```

Enable SSL certificate:
```bash
sudo certbot --nginx -d your-domain.com
sudo systemctl reload nginx
```

---

## 4. Environment Variables

Never store credentials in code or version control. Use environment variables exclusively.

Create `/opt/sl-middleware/.env` with restricted permissions:

```bash
sudo -u sl-middleware touch /opt/sl-middleware/.env
sudo chmod 600 /opt/sl-middleware/.env
```

Required variables:
```bash
# SoftLedger credentials
SOFTLEDGER_CLIENT_ID=your_client_id
SOFTLEDGER_CLIENT_SECRET=your_client_secret
SOFTLEDGER_TENANT_UUID=your_tenant_uuid

# Inbound API keys (generate with: python -c "import secrets; print(secrets.token_hex(32))")
MIDDLEWARE_API_KEY_1=generate_a_strong_random_key_here
MIDDLEWARE_API_KEY_2=second_key_for_rotation

# Webhook secret (for verifying inbound webhooks)
MIDDLEWARE_WEBHOOK_SECRET=generate_another_strong_secret

# IP restrictions (comma-separated, leave blank for no restriction)
ALLOWED_IPS=10.0.0.1,192.168.1.100

# SoftLedger account IDs for webhook auto-posting
SUSPENSE_ACCOUNT_ID=your_suspense_account_id
DEFAULT_LOCATION_ID=your_default_entity_id
WISE_BANK_ACCOUNT_ID=your_wise_bank_account_id
HSBC_BANK_ACCOUNT_ID=your_hsbc_bank_account_id
```

---

## 5. API Key Management

### Generating secure API keys

```python
import secrets
print(secrets.token_hex(32))  # 64-character hex key
```

### Key rotation policy

Rotate API keys every 90 days or immediately following any suspected compromise.

The middleware supports multiple simultaneous keys (MIDDLEWARE_API_KEY_1, _2, _3) to allow rotation without downtime:

1. Generate a new key
2. Add it as MIDDLEWARE_API_KEY_2
3. Update the calling system to use the new key
4. Remove the old key from MIDDLEWARE_API_KEY_1
5. Restart the service

### Never do this

- Share API keys over email or Slack
- Store API keys in code, comments or version control
- Use the same key for multiple environments (dev, staging, production)
- Log API keys (the middleware is designed to avoid this)

---

## 6. Production Deployment with Gunicorn

Install and configure Gunicorn as the WSGI server:

```bash
pip install gunicorn

# Run with multiple workers for resilience
gunicorn middleware.api_server:app \
    --workers 4 \
    --bind 127.0.0.1:8000 \
    --timeout 30 \
    --access-logfile logs/access.log \
    --error-logfile logs/error.log \
    --log-level warning
```

### Systemd service

Create `/etc/systemd/system/sl-middleware.service`:

```ini
[Unit]
Description=SoftLedger API Middleware
After=network.target

[Service]
Type=exec
User=sl-middleware
WorkingDirectory=/opt/sl-middleware
EnvironmentFile=/opt/sl-middleware/.env
ExecStart=/opt/sl-middleware/venv/bin/gunicorn middleware.api_server:app --workers 4 --bind 127.0.0.1:8000 --timeout 30
Restart=always
RestartSec=5

# Security restrictions
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/sl-middleware/logs

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable sl-middleware
sudo systemctl start sl-middleware
sudo systemctl status sl-middleware
```

---

## 7. Monitoring and Alerting

### Log monitoring

All requests are logged to `logs/middleware.log`. Monitor for:
- Failed authentication attempts (Invalid API key)
- Rate limit breaches (Rate limit exceeded)
- Unusual request volumes
- Failed journal entries

### Recommended monitoring stack

- **Uptime monitoring**: UptimeRobot (free) or Pingdom — monitors the /health endpoint
- **Log aggregation**: Papertrail or Logtail — aggregates and searches logs
- **Error alerting**: Sentry — captures and alerts on application errors
- **Server monitoring**: Netdata or Datadog — CPU, memory, disk and network

### Alert thresholds to configure

- More than 10 failed authentication attempts in 5 minutes
- Server CPU above 80% for more than 2 minutes
- Disk usage above 85%
- Any 500 errors in the middleware logs
- SSL certificate expiry within 14 days

---

## 8. Audit Trail

The middleware logs all requests and responses including:
- Timestamp
- Source IP address
- HTTP method and path
- Response status code
- Request duration
- Journal entry IDs created

Logs do NOT include:
- API keys or credentials
- Full request/response bodies
- Personal data

Retain logs for a minimum of 7 years to satisfy financial record-keeping requirements.

---

## 9. Penetration Testing Checklist

Before going live, verify:

- [ ] All endpoints require authentication (except /health)
- [ ] Rate limiting is active and tested
- [ ] SSL is enforced and redirects HTTP to HTTPS
- [ ] Security headers are present on all responses
- [ ] .env file is not accessible via web
- [ ] .git directory is blocked
- [ ] Server SSH is key-only (no password auth)
- [ ] Firewall allows only required ports
- [ ] Application runs as non-root user
- [ ] Logs are being written and rotated
- [ ] Alerting is configured and tested

---

## 10. Incident Response

If you suspect a breach or unauthorised access:

1. Immediately rotate all API keys
2. Rotate SoftLedger API credentials
3. Review middleware logs for the period in question
4. Check SoftLedger audit log for unauthorised journal entries
5. Notify your auditors and legal team if financial data was accessed
6. Document the incident and remediation steps

Contact SoftLedger support immediately if you believe SoftLedger credentials were compromised.

---

*This security guide should be reviewed and updated at least annually or following any significant infrastructure change.*
