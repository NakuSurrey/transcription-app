import requests
import time
import os
from dotenv import load_dotenv

# ============================================
# CONFIGURATION
# ============================================
load_dotenv()

API_TOKEN        = os.getenv("DO_API_TOKEN")
SSH_KEY_ID       = 54989881        # ← Replace with your actual numerical ID
GPU_SIZE         = "gpu-rtx4000-ada-1x"
IMAGE            = "ubuntu-22-04-x64"
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Every region that supports GPU droplets
REGIONS = ["nyc2", "sfo3", "atl1", "tor1", "ams3"]

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}"
}

POLL_INTERVAL = 30   # seconds

# ============================================
# TELEGRAM ALERT
# ============================================

def send_telegram(message):
    """Send a Telegram message to your personal chat."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("  ✅ Telegram message sent successfully")
        else:
            print(f"  ⚠️ Telegram failed: {response.text}")
    except Exception as e:
        print(f"  ⚠️ Telegram error: {e}")

# ============================================
# TEST TELEGRAM ON STARTUP
# ============================================

def test_telegram():
    """Send a test message so you know alerts are working."""
    print("  Sending Telegram test message...")
    send_telegram(
        "🤖 <b>GPU Sniper is now ACTIVE</b>\n\n"
        f"Targeting: {GPU_SIZE}\n"
        f"Regions: {', '.join(REGIONS)}\n"
        f"Polling every {POLL_INTERVAL} seconds\n\n"
        "I will message you the moment a GPU is claimed."
    )

# ============================================
# ATTEMPT TO CLAIM GPU IN ONE REGION
# ============================================

def try_claim(region):
    payload = {
        "name":     f"gpu-sniper-{region}",
        "region":   region,
        "size":     GPU_SIZE,
        "image":    IMAGE,
        "ssh_keys": [54989881],
        "tags":     ["gpu-hunter"]
    }

    try:
        response = requests.post(
            "https://api.digitalocean.com/v2/droplets",
            headers=HEADERS,
            json=payload,
            timeout=15
        )

        if response.status_code == 202:
            # SUCCESS — GPU claimed
            data = response.json()
            droplet_id = data["droplet"]["id"]
            droplet_name = data["droplet"]["name"]

            # Print to terminal
            print("\n" + "=" * 50)
            print(f"  🎯 GPU CLAIMED in {region.upper()}!")
            print(f"  Droplet ID: {droplet_id}")
            print("=" * 50 + "\n")

            # Send Telegram alert
            send_telegram(
                f"🎯 <b>GPU CLAIMED!</b>\n\n"
                f"Region: <b>{region.upper()}</b>\n"
                f"Droplet ID: <b>{droplet_id}</b>\n"
                f"Name: {droplet_name}\n\n"
                f"⚡ Go to DigitalOcean dashboard NOW\n"
                f"Get your IP and SSH in:\n"
                f"ssh root@YOUR_NEW_IP\n\n"
                f"⚠️ Billing has started!"
            )
            return True

        elif response.status_code == 422:
            print(f"  [{region.upper()}] Out of stock — waiting...")
            return False

        elif response.status_code == 403:
            print(f"  [{region.upper()}] Auth failed — check your API token")
            send_telegram("⚠️ GPU Sniper: Auth failed — check your API token")
            return False

        else:
            print(f"  [{region.upper()}] Unexpected {response.status_code}: {response.text[:100]}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"  [{region.upper()}] Network error: {e} — will retry")
        return False

# ============================================
# MAIN SNIPER LOOP
# ============================================

def run_sniper():
    attempt = 0

    print("\n" + "=" * 50)
    print("  GPU SNIPER ACTIVE")
    print(f"  Targeting: {GPU_SIZE}")
    print(f"  Regions:   {', '.join(REGIONS)}")
    print(f"  Polling every {POLL_INTERVAL} seconds")
    print("  Press Ctrl+C to stop")
    print("=" * 50 + "\n")

    # Test Telegram works before we start
    test_telegram()

    while True:
        attempt += 1
        print(f"[Attempt #{attempt}] Checking all regions...")

        for region in REGIONS:
            success = try_claim(region)

            if success:
                print("Sniper complete. Script stopped.")
                print("DO NOT run this again or you will buy a second GPU.")
                return

            time.sleep(2)  # Small gap between region checks

        print(f"  All regions dry. Next check in {POLL_INTERVAL} seconds...\n")
        time.sleep(POLL_INTERVAL)

# ============================================
# ENTRY POINT
# ============================================

if __name__ == "__main__":
    if not API_TOKEN:
        print("ERROR: DO_API_TOKEN not found in .env file")
    elif not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found in .env file")
    elif not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not found in .env file")
    else:
        run_sniper()