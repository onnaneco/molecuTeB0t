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
SOURCES_PRIORITY = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name, iupac=""):
    """Strictly filters out DB IDs, technical codes, and IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    u = name.upper()
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # CAS Numbers
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True # Technical IUPAC
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
    print("Searching for a diverse molecule...")

    # We check the first 50,000 CIDs. These are the most well-documented in the world.
    # They include drugs, but also pigments, industrial chemicals, and natural substances.
    all_potential = list(range(1, 50000))
    random.shuffle(all_potential)

    # Process in batches of 50 for extreme speed
    for i in range(0, 1000, 50):
        batch = all_potential[i:i+50]
        batch_str = ",".join(map(str, batch))
        
        try:
            # Batch fetch descriptions (High-speed endpoint)
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{batch_str}/description/JSON"
            r = requests.get(url, timeout=15)
            if r.status_code != 200: continue
            
            infos = r.json().get('InformationList', {}).get('Information', [])
            
            # Organize descriptions by CID
            desc_map = {}
            for info in infos:
                cid = info.get('CID')
                if not cid or str(cid) in used: continue
                
                text = info.get('Description', '')
                source = info.get('DescriptionSourceName', '')
                
                for target in SOURCES_PRIORITY:
                    target_clean = target.split('(')[0].strip().lower()
                    if target_clean in source.lower():
                        words = text.split()
                        if 10 < len(words) <= 150: # Word limit
                            if cid not in desc_map: desc_map[cid] = {}
                            desc_map[cid][target] = text

            # Check candidates that have a valid description
            candidates = list(desc_map.keys())
            random.shuffle(candidates)
            
            for cid in candidates:
                # Fetch Name, Synonyms, and IUPAC
                prop_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
                syn_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
                
                iupac = prop_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
                syns = syn_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
                
                # Filter for clean common names
                clean_names = [s for s in syns if not is_bad_name(s, iupac)]
                if not clean_names: continue

                # Pick best description based on priority
                final_desc = None
                for target in SOURCES_PRIORITY:
                    if target in desc_map[cid]:
                        final_desc = desc_map[cid][target]
                        break
                
                if final_desc:
                    return {
                        "name": clean_names[0],
                        "aka": clean_names[1:4],
                        "iupac": iupac,
                        "description": final_desc,
                        "cid": cid,
                        "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
                    }
        except: continue

    print("No molecule found.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape Markdown V1 to prevent errors
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

    caption = (
        f"*Name:* {clean(data['name'])}\n"
        f"*A.K.A.:* {clean(aka_text)}\n"
        f"*IUPAC Name:* {clean(data['iupac'])}\n\n"
        f"*Description:* {clean(data['description'])}\n\n"
        f"*PubChem Link:* https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}"
    )

    payload = {"chat_id": CHANNEL_ID, "photo": data['image'], "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code == 200:
        save_cid(data['cid'])
        print(f"Posted: {data['name']}")
    else:
        # Fallback if markdown fails
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
