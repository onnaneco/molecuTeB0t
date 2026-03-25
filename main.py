import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Priority for Description sources
TARGET_SOURCES = [
    "DrugBank", 
    "NCI Thesaurus (NCIt)", 
    "Medical Subject Headings (MeSH)", 
    "CAMEO Chemicals"
]

def is_bad_name(name, iupac=""):
    """Filters out database IDs, technical codes, and IUPAC strings."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # Comprehensive blacklist of DB prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # Filter CAS numbers
    
    # Heuristic: If name is mostly numbers/dashes or all caps with numbers, it's an ID
    if sum(c.isdigit() for c in name) > (len(name) / 2): return True
    if u == name and any(c.isdigit() for c in name) and len(name) > 6: return True

    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_molecule_data():
    used = get_used_cids()
    print("Searching for a diverse molecule (Batch Mode)...")
    
    while True:
        # Step 1: Pick 50 random CIDs from the most data-rich range (1 to 1,000,000)
        batch = [random.randint(1, 1000000) for _ in range(50)]
        batch_str = ",".join(map(str, batch))
        
        # Step 2: Fetch descriptions for the entire batch in ONE request (High Speed)
        try:
            desc_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{batch_str}/description/JSON"
            r = requests.get(desc_url, timeout=15)
            if r.status_code != 200: continue
            
            # Map descriptions to CIDs
            all_data = r.json().get('InformationList', {}).get('Information', [])
            cid_to_desc = {}
            for info in all_data:
                cid = info.get('CID')
                if not cid or str(cid) in used: continue
                
                text = info.get('Description')
                source = info.get('DescriptionSourceName', '')
                
                if text and source:
                    # Check if source matches priority
                    for target in TARGET_SOURCES:
                        target_base = target.split('(')[0].strip().lower()
                        if target_base in source.lower():
                            word_count = len(text.split())
                            if 10 < word_count <= 150:
                                if cid not in cid_to_desc: cid_to_desc[cid] = []
                                cid_to_desc[cid].append({'text': text, 'source': target})

            # Step 3: Check CIDs that passed the description filter
            valid_cids = list(cid_to_desc.keys())
            random.shuffle(valid_cids)

            for cid in valid_cids:
                # Get IUPAC and Synonyms for this candidate
                prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
                syn_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
                
                p_res = requests.get(prop_url, timeout=5).json()
                s_res = requests.get(syn_url, timeout=5).json()
                
                iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
                syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
                
                clean_names = [s for s in syns if not is_bad_name(s, iupac)]
                if not clean_names: continue
                
                # Pick the best description based on target priority
                final_desc = None
                available = cid_to_desc[cid]
                for target in TARGET_SOURCES:
                    for item in available:
                        if item['source'] == target:
                            final_desc = item['text']
                            break
                    if final_desc: break

                return {
                    "name": clean_names[0],
                    "aka": clean_names[1:4],
                    "iupac": iupac,
                    "description": final_desc,
                    "cid": cid,
                    "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
                }
        except Exception as e:
            print(f"Batch failed, retrying... ({e})")
            continue

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

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
        print(f"Successfully posted: {data['name']}")
    else:
        # Fallback for complex names
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
