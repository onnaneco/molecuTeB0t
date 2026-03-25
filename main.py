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
SOURCES_PRIORITY = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name, iupac_name=""):
    """Strictly filters out DB IDs, codes, and technical IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac_name and name.lower() == iupac_name.lower(): return True
    
    u = name.upper()
    # Database ID prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS", "ST0"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # Filter CAS numbers or numeric codes (e.g. 100-22-1)
    if re.match(r'^[0-9\-]{5,}$', name): return True

    # Filter technical IUPAC names (heuristic: too many numbers/brackets/dashes)
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True
    
    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_description_deep(cid):
    """Deep-crawls the PUG VIEW JSON for the specific curated sources."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200: return None, None
        data = r.json()
        found_map = {}

        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        val_list = info.get('Value', {}).get('StringWithMarkup', [])
                        if not val_list: continue
                        text = val_list[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            for s in SOURCES_PRIORITY:
                                # Match priority sources (e.g. NCIt, MeSH)
                                s_clean = s.split('(')[0].strip().lower()
                                if s_clean in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 150: # User limit: 150 words
                                        if s not in found_map:
                                            found_map[s] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])

        crawl(data)
        # Return based on strict priority
        for s in SOURCES_PRIORITY:
            if s in found_map: return found_map[s], s
    except: pass
    return None, None

def get_molecule_data():
    used = get_used_cids()
    print("Searching PubChem indefinitely for a perfect molecule...")
    
    attempts = 0
    while True: # UNLIMITED ATTEMPTS
        attempts += 1
        cid = random.randint(1, 1000000) # Diverse range
        
        if str(cid) in used: continue
        
        # Log progress every 10 tries
        if attempts % 10 == 0:
            print(f"  Attempt {attempts}: Checking CID {cid}...")

        try:
            # 1. Fetch IUPAC Name (Necessary for filtering)
            p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            p_res = requests.get(p_url, timeout=5).json()
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "")
            
            # 2. Fetch Synonyms
            s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            s_res = requests.get(s_url, timeout=5).json()
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not syns: continue

            # 3. Filter for Common Names (Reject IUPAC and DB IDs)
            clean_names = [s for s in syns if not is_bad_name(s, iupac)]
            if not clean_names: continue
            
            # 4. Deep Search for Description (Priority Sources)
            desc, source = get_description_deep(cid)
            if not desc: continue

            # Success! Build the package
            return {
                "name": clean_names[0],
                "aka": clean_names[1:4], # First 3 synonyms that aren't IUPAC/IDs
                "iupac": iupac or "N/A",
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except:
            time.sleep(0.2) # Small delay to respect API limits
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

    payload = {
        "chat_id": CHANNEL_ID, 
        "photo": data['image'], 
        "caption": caption, 
        "parse_mode": "Markdown"
    }
    
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code == 200:
        save_cid(data['cid'])
        print(f"DONE! Posted {data['name']} (CID: {data['cid']})")
    else:
        # Fallback if markdown is broken by chemical symbols
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
