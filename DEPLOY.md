# Deploying Combined Arbitrage Scanner to DigitalOcean

## Overview

This bot runs two complementary strategies:
1. **Instant Arbitrage** - Scans orderbook spreads every 5 min (free)
2. **Base Rate Arbitrage** - Researches mispriced markets hourly (~$0.03/market)

## Requirements

- DigitalOcean droplet: **2GB RAM minimum** ($12/month)
  - For instant-only mode: 1GB works ($6/month)
- Ubuntu 22.04
- Docker & Docker Compose

---

## Quick Start

### 1. Create Droplet

- Go to: DigitalOcean → Create → Droplets
- **Image**: Ubuntu 22.04
- **Plan**: Basic, **2GB RAM / 1 CPU** ($12/month)
- **Region**: US East (closer to Kalshi servers)
- Add your SSH key
- Create

### 2. SSH In

```bash
ssh root@YOUR_DROPLET_IP
```

### 3. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
```

### 4. Clone & Configure

```bash
cd /opt
git clone https://github.com/katherineworden/baserate-arb.git
cd baserate-arb

# Create .env with your keys
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-your-key-here
KALSHI_API_KEY=your-kalshi-api-key
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
paste-your-private-key-here
-----END RSA PRIVATE KEY-----"
KALSHI_API_SECRET="-----BEGIN RSA PRIVATE KEY-----
paste-same-key-here
-----END RSA PRIVATE KEY-----"
MIN_LIQUIDITY=5000
AUTO_EXECUTE=false
EOF

chmod 600 .env
```

### 5. Start Scanner

```bash
# Build and run (detached)
docker compose up -d --build

# Watch logs
docker compose logs -f scanner
```

---

## Deployment Options

### Option A: Combined Scanner (Recommended)

Both instant + base rate strategies:
```bash
docker compose up -d scanner
```

### Option B: Instant-Only (Cheaper)

No Claude API costs, just orderbook scanning:
```bash
docker compose --profile instant up -d
```

### Option C: With Web Dashboard

Adds web UI on port 8000:
```bash
docker compose --profile web up -d
```

Access: `http://YOUR_IP:8000`

---

## Managing the Bot

### View Logs
```bash
docker compose logs -f scanner          # Live logs
docker compose logs --tail 100 scanner  # Last 100 lines
```

### Check Report
```bash
docker compose exec scanner python run_combined.py --report
```

### Restart / Stop
```bash
docker compose restart scanner
docker compose down
```

### Update Code
```bash
cd /opt/baserate-arb
git pull
docker compose up -d --build
```

---

## Monitoring

### Check Status
```bash
docker compose ps
docker stats arb-scanner
```

### Paper Trade Results
```bash
cat data/paper_trades.json | python3 -m json.tool | tail -50
```

### Scanner Stats
```bash
cat data/scanner_stats.json
```

---

## Cost Breakdown

| Item | Cost |
|------|------|
| 2GB Droplet | $12/month |
| Kalshi API | Free |
| Claude (base rate) | ~$2/day* |

*At default 3 markets/hour. Use `--instant` mode for $0 API costs.

---

## Going Live (Real Trading)

**Before enabling real trades:**

1. Paper trade for 1-2 weeks minimum
2. Check `data/paper_trades.json` shows positive results
3. Verify win rate and total profit
4. Start very small

To enable real trading (DANGEROUS):
```bash
# Edit .env
AUTO_EXECUTE=true

# Restart with confirmation
docker compose down
docker compose up -d
```

---

## Useful Commands

```bash
# Single scan (no continuous loop)
docker compose exec scanner python run_combined.py --once

# Instant scan only
docker compose exec scanner python run_combined.py --once --instant

# Base rate scan only
docker compose exec scanner python run_combined.py --once --baserate

# Performance report
docker compose exec scanner python run_combined.py --report
```

---

## Troubleshooting

### No Opportunities Found
- Normal during off-hours / weekends
- Kalshi markets have limited liquidity sometimes
- Check that markets have actual bid/ask prices

### API Errors
- Verify `.env` keys are correct
- Check Kalshi API status
- Rate limits are handled automatically

### Out of Memory
- Upgrade to 2GB+ droplet
- Or use instant-only mode

### Container Won't Start
```bash
docker compose logs scanner
docker compose up -d --build --force-recreate
```
