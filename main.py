import requests
import random
import sys
import os
import re
import time

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Description Priority Sources
TARGET_SOURCES = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name, iupac=""):
    """Filters out database IDs, technical codes, and technical IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS", "BRN", "EINECS"]
    
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # CAS Numbers
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True # Technical IUPAC
    return False

def get_used_cids():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_molecule_data():
    used = get_used_cids()
    print("Searching for a diverse molecule...")
    
    attempts = 0
    # Search until a winner is found. Range 1-1,000,000 contains all high-quality data.
    while True:
        attempts += 1
        cid = random.randint(1, 1000000)
        if str(cid) in used: continue
        
        if attempts % 20 == 0:
            print(f"  - Attempt {attempts}: Checking CID {cid}")

        try:
            # 1. Fetch Synonyms and IUPAC
            prop_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
            syn_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
            
            iupac = prop_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "")
            syns = syn_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not syns: continue
            
            # Clean names and separate AKA
            clean_names = [s for s in syns if not is_bad_name(s, iupac)]
            if not clean_names: continue

            # 2. Fetch Description (Fast Description Endpoint)
            desc_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/description/JSON", timeout=5).json()
            infos = desc_res.get('InformationList', {}).get('Information', [])
            
            found_desc = {}
            for info in infos:
                text = info.get('Description')
                source = info.get('DescriptionSourceName', '')
                if text and source:
                    for target in TARGET_SOURCES:
                        # Match target source name
                        if target.split('(')[0].strip().lower() in source.lower():
                            if len(text.split()) <= 150: # Word limit
                                if target not in found_desc: found_desc[target] = text

            # Pick best description by priority
            final_desc = None
            for target in TARGET_SOURCES:
                if target in found_desc:
                    final_desc = found_desc[target]
                    break
            
            if not final_desc: continue

            # Success!
            return {
                "name": clean_names[0],
                "aka": clean_names[1:4],
                "iupac": iupac or "N/A",
                "description": final_desc,
                "cid": cid
            }
        except:
            continue

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`").replace("(", "\\(").replace(")", "\\)")

    caption = (
        f"*Name:* {clean(data['name'])}\n"
        f"*A.K.A.:* {clean(aka_text)}\n"
        f"*IUPAC Name:* {clean(data['iupac'])}\n\n"
        f"*Description:* {clean(data['description'])}\n\n"
        f"*PubChem Link:* https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}"
    )

    image_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{data['cid']}/PNG"
    payload = {"chat_id": CHANNEL_ID, "photo": image_url, "caption": caption, "parse_mode": "Markdown"}
    
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code != 200:
        # Fallback for complex chemical notation
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    
    save_cid(data['cid'])
    print(f"Posted: {data['name']}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
