import os
import sys
import json
import random
import requests
import html

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"
DB_FILE = "molecules.json"

def get_used_cids():
    """Reads the history file to get already posted CIDs."""
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_cid(cid):
    """Appends the posted CID to the history file."""
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{cid}\n")

def post_to_telegram(data):
    """Formats the molecule data and sends it to the Telegram channel."""
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_TOKEN environment variable is missing.")
        sys.exit(1)

    # Format the A.K.A. list
    aka_list = data.get('aka',[])
    aka_text = ", ".join(aka_list) if aka_list else "None"
    
    # Get the tag (remove the # if it already has one, to prevent ##tag)
    tag = data.get('tag', 'molecule').replace("#", "")

    # Construct the caption using HTML formatting
    # html.escape() ensures that chemical names with < or > don't crash Telegram
    caption = (
        f"<b>Name:</b> {html.escape(data.get('name', 'N/A'))}\n"
        f"<b>A.K.A.:</b> {html.escape(aka_text)}\n"
        f"<b>IUPAC Name:</b> {html.escape(data.get('iupac', 'N/A'))}\n\n"
        f"<b>Description:</b> {html.escape(data.get('description', 'N/A'))}\n\n"
        f"<b>PubChem Link:</b> https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}\n\n"
        f"#{tag}"
    )

    # High-quality 2D structure image from PubChem
    image_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{data['cid']}/PNG?record_type=2d&image_size=large"
    
    payload = {
        "chat_id": CHANNEL_ID, 
        "photo": image_url, 
        "caption": caption, 
        "parse_mode": "HTML"
    }
    
    # Send the request to Telegram API
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    r = requests.post(url, data=payload)
    
    if r.status_code == 200:
        save_cid(data['cid'])
        print(f"Successfully posted: {data['name']} (CID: {data['cid']})")
    else:
        print(f"Failed to post. Telegram API Response: {r.text}")
        sys.exit(1)

if __name__ == "__main__":
    # 1. Load the database
    if not os.path.exists(DB_FILE):
        print(f"Error: {DB_FILE} not found!")
        sys.exit(1)
        
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            molecules = json.load(f)
        except json.JSONDecodeError:
            print(f"Error: {DB_FILE} is not a valid JSON file.")
            sys.exit(1)

    # 2. Get history and filter available molecules
    used_cids = get_used_cids()
    available_molecules =[m for m in molecules if str(m["cid"]) not in used_cids]

    if not available_molecules:
        print("All molecules in the JSON file have been posted! Time to add more.")
        sys.exit(0)

    # 3. Pick a random molecule and post it
    selected_molecule = random.choice(available_molecules)
    post_to_telegram(selected_molecule)
