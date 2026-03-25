import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Priority for Description sources (Headings used in PubChem Annotations)
SOURCE_HEADINGS = [
    ("DrugBank Description", "DrugBank"),
    ("NCI Thesaurus Description", "NCI Thesaurus (NCIt)"),
    ("MeSH Description", "Medical Subject Headings (MeSH)"),
    ("CAMEO Chemicals Description", "CAMEO Chemicals")
]

def is_bad_name(name, iupac):
    """Filters out database IDs, codes, and technical IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    # 1. Database Prefix Blacklist
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"]
    u = name.upper()
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # 2. Heuristic: Real names usually have lowercase letters.
    # IDs (SCHEMBL123) and codes (A-123) are usually ALL CAPS.
    if u == name and not re.match(r'^[A-Z]{2,4}$', name): # Allow short acronyms like ATP, EDTA
        if len(name) > 5: return True

    # 3. Technical IUPAC heuristic (too many numbers/brackets)
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

def get_cid_pool():
    """Fetches all CIDs that have the curated descriptions we want."""
    print("Fetching high-quality molecule pool from PubChem Annotations...")
    pool = []
    # We fetch CIDs for all 4 sources to ensure diversity
    for heading, _ in SOURCE_HEADINGS:
        try:
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/{heading.replace(' ', '%20')}/JSON"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                annotations = r.json().get('Annotations', {}).get('Annotation', [])
                for ann in annotations:
                    cids = ann.get('LinkedRecords', {}).get('CID', [])
                    pool.extend(cids)
        except: continue
    return list(set(pool))

def get_molecule_details(cid):
    """Fetches Name, AKA, IUPAC, and Description."""
    try:
        # 1. Get Synonyms and IUPAC
        prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
        syn_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
        
        iupac = requests.get(prop_url).json().get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "")
        syns = requests.get(syn_url).json().get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])

        # Filter names
        clean_names = [s for s in syns if not is_bad_name(s, iupac)]
        if not clean_names: return None

        # 2. Get Descriptions (Priority Search)
        desc_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/description/JSON"
        desc_data = requests.get(desc_url).json().get('InformationList', {}).get('Information', [])
        
        best_desc = None
        # Check priority: DrugBank > NCIt > MeSH > CAMEO
        for _, source_label in SOURCE_HEADINGS:
            for info in desc_data:
                source_name = info.get('DescriptionSourceName', '')
                if source_label.lower() in source_name.lower():
                    text = info.get('Description', '')
                    words = text.split()
                    if 10 < len(words) <= 150:
                        best_desc = text
                        break
            if best_desc: break

        if not best_desc: return None

        return {
            "name": clean_names[0],
            "aka": clean_names[1:4],
            "iupac": iupac or "N/A",
            "description": best_desc,
            "cid": cid,
            "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
        }
    except: return None

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

    payload = {"chat_id": CHANNEL_ID, "photo": data['image'], "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code == 200:
        save_cid(data['cid'])
        print(f"Posted: {data['name']}")
    else:
        # Fallback for broken markdown
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    used = get_used_cids()
    pool = get_cid_pool()
    random.shuffle(pool)
    
    found = False
    print(f"Checking {len(pool)} candidates...")
    for cid in pool:
        if str(cid) in used: continue
        
        data = get_molecule_details(cid)
        if data:
            post_to_telegram(data)
            found = True
            break
            
    if not found:
        print("No suitable molecule found.")
        sys.exit(1)
