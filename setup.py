# setup.py — Run this ONCE after unzipping the project
# Initializes Git repo, creates venv, installs dependencies, first commit
#
# USAGE: Open terminal in transcription-app/ folder, run:
#   python setup.py
#
# WHAT IT DOES (in this exact order):
#   1. Initialize Git repo
#   2. .gitignore is already in place (shield is UP)
#   3. Create virtual environment
#   4. Install dependencies
#   5. First commit — your save point

import subprocess
import sys
import os

def run(cmd, description):
    """Run a command and print what's happening."""
    print(f"\n{'='*50}")
    print(f"  {description}")
    print(f"{'='*50}")
    print(f"  Running: {cmd}\n")
    result = subprocess.run(cmd, shell=True, capture_output=False)
    if result.returncode != 0:
        print(f"  WARNING: Command returned non-zero exit code: {result.returncode}")
    return result.returncode

def main():
    # Make sure we're in the project root
    if not os.path.exists(".gitignore"):
        print("ERROR: Run this script from the transcription-app/ folder!")
        print("  cd transcription-app")
        print("  python setup.py")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("  TRANSCRIPTION APP — FIRST-TIME SETUP")
    print("=" * 50)

    # Step 1: Initialize Git repo
    # .gitignore already exists — shield is up BEFORE first commit
    run("git init", "Initializing Git repository")

    # Step 2: Create virtual environment
    run(f"{sys.executable} -m venv venv", "Creating virtual environment")

    # Step 3: Determine pip path based on OS
    if sys.platform == "win32":
        pip_path = os.path.join("venv", "Scripts", "pip")
        activate_hint = "venv\\Scripts\\activate"
    else:
        pip_path = os.path.join("venv", "bin", "pip")
        activate_hint = "source venv/bin/activate"

    # Step 4: Install dependencies
    run(f"{pip_path} install -r requirements.txt", "Installing dependencies")

    # Step 5: First commit
    run("git add .", "Staging all files for first commit")
    run('git config user.email "you@example.com"', "Setting Git email (change this)")
    run('git config user.name "Your Name"', "Setting Git name (change this)")
    run('git commit -m "Phase 1-5: Complete project skeleton"',
        "First commit — your undo button is now active")

    print("\n" + "=" * 50)
    print("  SETUP COMPLETE!")
    print("=" * 50)
    print(f"""
  Next steps:
  
  1. Activate your virtual environment:
     {activate_hint}
  
  2. Fill in your real secrets in .env:
     - DO_API_TOKEN (from Digital Ocean dashboard)
     - DO_DROPLET_ID (your GPU droplet ID)
     - SERVER_IP (your droplet's IP address)
  
  3. Connect to your private GitHub repo:
     git remote add origin https://github.com/YOUR_USERNAME/transcription-app.git
     git branch -M main
     git push -u origin main
  
  4. Run the app:
     cd client
     python main.py
""")

if __name__ == "__main__":
    main()
