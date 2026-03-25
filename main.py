import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Description Priority Sources
PRIORITY_SOURCES = [
    {"name": "DrugBank", "label": "DrugBank"},
    {"name": "NCI Thesaurus", "label": "NCI Thesaurus (NCIt)"},
    {"name": "Medical Subject Headings", "label": "Medical Subject Headings (MeSH)"},
    {"name": "CAMEO Chemicals", "label": "CAMEO Chemicals"}
]

def is_bad_name(name, iupac=""):
    """Aggressively filters out database IDs and technical codes."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # Database ID prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # Filter CAS or purely numeric codes
    if re.match(r'^[0-9\-]{5,}$', name): return True
    
    # Filter strings that are all caps + numbers and > 7 chars (usually IDs)
    if u == name and any(c.isdigit() for c in name) and len(name) > 7: return True

    # Technical IUPAC heuristic (too many numbers/brackets)
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 7: return True

    return False

def get_posted_cids():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{cid}\n")

def get_molecule_details(cid):
    """Fetches and validates the molecule data."""
    try:
        # 1. Fetch IUPAC and Synonyms (Fast REST)
        p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
        s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
        
        p_res = requests.get(p_url, timeout=5).json()
        s_res = requests.get(s_url, timeout=5).json()
        
        iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
        syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
        
        # Determine Clean Names
        clean_names = [s for s in syns if not is_bad_name(s, iupac)]
        if not clean_names: return None

        # 2. Fetch Descriptions (High-speed Description API)
        d_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/description/JSON"
        d_res = requests.get(d_url, timeout=5).json()
        infos = d_res.get('InformationList', {}).get('Information', [])
        
        found_desc = {}
        for info in infos:
            text = info.get('Description')
            source = info.get('DescriptionSourceName', '')
            if text and source:
                for target in PRIORITY_SOURCES:
                    if target['name'].lower() in source.lower():
                        if len(text.split()) <= 150:
                            if target['label'] not in found_desc:
                                found_desc[target['label']] = text
        
        # Select by Priority
        final_desc = None
        for target in PRIORITY_SOURCES:
            if target['label'] in found_desc:
                final_desc = found_desc[target['label']]
                break
        
        if not final_desc: return None

        return {
            "name": clean_names[0],
            "aka": clean_names[1:4], # Next 3 clean names
            "iupac": iupac,
            "description": final_desc,
            "cid": cid
        }
    except: return None

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape Markdown characters
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
        # Fallback for complex names breaking Markdown
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    
    save_posted_cid(data['cid'])
    print(f"Posted: {data['name']}")

if __name__ == "__main__":
    posted = get_posted_cids()
    print("Searching for a diverse molecule...")
    
    # Search the first 500,000 CIDs (The range with most descriptions)
    attempts = 0
    while True:
        attempts += 1
        cid = random.randint(1, 500000)
        if str(cid) in posted: continue
        
        if attempts % 10 == 0: print(f"Attempt {attempts}...")
        
        mol = get_molecule_details(cid)
        if mol:
            post_to_telegram(mol)
            break
