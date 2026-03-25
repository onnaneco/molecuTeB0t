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

# Priority for Description sources
SOURCES_PRIORITY = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name):
    """Filters out database IDs and technical codes."""
    if not name or len(name) < 3: return True
    u = name.upper()
    # Expanded database ID blacklist
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # Filter CAS numbers
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 10: return True # Filter technical IUPAC
    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_description(cid):
    """Deep-crawls the PUG VIEW JSON for specific curated sources."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200: return None, None
        data = r.json()
        found = {}

        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                # Search for Information blocks that contain text and a reference
                if 'Information' in node:
                    for info in node['Information']:
                        val_list = info.get('Value', {}).get('StringWithMarkup', [])
                        if not val_list: continue
                        text = val_list[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            for s in SOURCES_PRIORITY:
                                # Check if source name exists in the reference string
                                # e.g., "NCI Thesaurus (NCIt)" matches "NCIt" or "NCI Thesaurus"
                                simplified_s = s.split('(')[0].strip().lower()
                                if simplified_s in ref.lower():
                                    words = text.split()
                                    if 5 < len(words) <= 150:
                                        if s not in found: found[s] = text
                # Recurse through all sections
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(data)
        # Return the best match based on priority list
        for s in SOURCES_PRIORITY:
            if s in found: return found[s], s
    except: pass
    return None, None

def get_molecule_data():
    used = get_used_cids()
    print("Searching for a molecule with high-quality data...")
    
    # We search the range 1-50,000 because these are the most documented compounds
    # This range is virtually guaranteed to contain thousands of valid molecules.
    search_pool = list(range(1, 50000))
    random.shuffle(search_pool)
    
    attempts = 0
    for cid in search_pool:
        if str(cid) in used: continue
        attempts += 1
        
        # Limit search to 300 attempts to prevent workflow timeout, 
        # but 300 is plenty for this CID range.
        if attempts > 300: break 
        
        try:
            # 1. Fetch IUPAC and Synonyms
            p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_res = requests.get(p_url, timeout=5).json()
            s_res = requests.get(s_url, timeout=5).json()
            
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not iupac or not syns: continue

            # 2. Filter Name and A.K.A.
            # Names must NOT be technical IUPAC and NOT be bad database IDs
            clean_names = [s for s in syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            
            if not clean_names: continue
            
            # 3. Fetch Description from Curated Sources
            desc, source_found = get_description(cid)
            if not desc: continue

            # Final Data Formatting
            return {
                "name": clean_names[0],
                "aka": clean_names[1:4], # Up to 3 synonyms
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except:
            continue

    print("Error: Could not find a suitable molecule after many attempts.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

    # Layout as requested
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
        print(f"Posted: {data['name']} (CID: {data['cid']})")
    else:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
