# Deploying Base Rate Arb to DigitalOcean

## Quick Start (DigitalOcean Droplet)

### 1. Create a Droplet
- Go to DigitalOcean → Create → Droplets
- Choose: Ubuntu 22.04, Basic, $6/month (1GB RAM is fine)
- Add your SSH key
- Create Droplet

### 2. SSH into your Droplet
```bash
ssh root@YOUR_DROPLET_IP
```

### 3. Install Docker
```bash
curl -fsSL https://get.docker.com | sh
```

### 4. Clone the repo
```bash
git clone https://github.com/katherineworden/baserate-arb.git
cd baserate-arb
```

### 5. Create .env file with your keys
```bash
cat > .env << 'EOF'
ANTHROPIC_API_KEY=your-anthropic-api-key-here
KALSHI_API_KEY=your-kalshi-api-key-here
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
your-private-key-content-here
-----END RSA PRIVATE KEY-----"
SCAN_INTERVAL_MINUTES=60
EOF
chmod 600 .env
```

### 6. Start the bot
```bash
# Paper trading mode (safe)
docker-compose up -d

# Check logs
docker-compose logs -f
```

### 7. Monitor
```bash
# View status
docker exec baserate-arb python run_trader.py status

# Generate report
docker exec baserate-arb python run_trader.py report --period daily

# View logs
docker-compose logs -f --tail 100
```

---

## Alternative: DigitalOcean App Platform

For easier management, use App Platform:

1. Fork the repo to your GitHub
2. Go to DigitalOcean → Apps → Create App
3. Connect to your GitHub repo
4. Add environment variables (API keys)
5. Deploy

Cost: ~$5/month for basic tier

---

## Email Reports (Optional)

To get daily/weekly reports via email, add to .env:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
REPORT_EMAIL=your-email@gmail.com
```

For Gmail, create an "App Password" at: https://myaccount.google.com/apppasswords

---

## Going Live (Real Money)

**Before switching to live trading:**

1. Paper trade for at least 1-2 weeks
2. Verify the strategy shows positive edge
3. Check win rate and drawdowns
4. Start with small amounts ($50-100)

To switch from paper to live:
```bash
# In .env
PAPER_TRADING=false

# Restart
docker-compose down && docker-compose up -d
```

---

## Useful Commands

```bash
# Stop the bot
docker-compose down

# View real-time logs
docker-compose logs -f

# Run a manual scan
docker exec baserate-arb python scheduler.py --once

# Generate weekly report
docker exec baserate-arb python scheduler.py --report weekly

# Check paper trading status
docker exec baserate-arb python run_trader.py paper status

# Reset paper trading account
docker exec baserate-arb python run_trader.py paper reset --balance 1000
```
