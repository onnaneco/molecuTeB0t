import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

def is_bad_name(name, iupac=""):
    """Filters out database IDs (SCHEMBL, ZINC, etc) and technical IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # Comprehensive DB ID prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # Filter CAS
    
    # Technical name heuristic (lots of numbers/brackets)
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True
    
    # If name is ALL CAPS + numbers and long, it's a DB ID
    if u == name and any(c.isdigit() for c in name) and len(name) > 6: return True

    return False

def get_used_cids():
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_molecule_details(cid):
    """Fetches and filters molecule data based on your exact layout requirements."""
    try:
        # 1. Fetch IUPAC and Synonyms
        p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=10).json()
        s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=10).json()
        
        iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
        syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
        
        # Filter for clean common names (No IUPAC, No SCHEMBL)
        clean_names = [s for s in syns if not is_bad_name(s, iupac)]
        if not clean_names: return None

        # 2. Fetch Description (PUG VIEW) - Search for specific sources
        v_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        v_res = requests.get(v_url, timeout=15).json()
        
        found_descriptions = {}
        
        def crawl(node):
            if isinstance(node, list):
                for item in node: crawl(item)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        text = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            # Map to your requested priority sources
                            targets = {
                                "DrugBank": "DrugBank",
                                "NCI Thesaurus": "NCI Thesaurus (NCIt)",
                                "Medical Subject Headings": "Medical Subject Headings (MeSH)",
                                "CAMEO Chemicals": "CAMEO Chemicals"
                            }
                            for key, label in targets.items():
                                if key.lower() in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 150:
                                        if label not in found_descriptions:
                                            found_descriptions[label] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])

        crawl(v_res)
        
        # Apply strict priority
        final_desc = None
        for p in ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]:
            if p in found_descriptions:
                final_desc = found_descriptions[p]
                break
        
        if not final_desc: return None

        return {
            "name": clean_names[0],
            "aka": clean_names[1:4], # Up to 3
            "iupac": iupac,
            "description": final_desc,
            "cid": cid,
            "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
        }
    except: return None

def get_cid_pool():
    """Triple-fallback pool generation."""
    # 1. Try Annotation Index (Molecules with descriptions)
    try:
        r = requests.get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/Record%20Description/JSON", timeout=10)
        if r.status_code == 200:
            anns = r.json().get('Annotations', {}).get('Annotation', [])
            cids = [c for a in anns for c in a.get('LinkedRecords', {}).get('CID', [])]
            if cids: return list(set(cids))
    except: pass

    # 2. Fallback: Famous Chemical Range (CIDs 1-20,000)
    # This range has dyes, flavorings, poisons, and common chemicals.
    return list(range(1, 20000))

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

    payload = {"chat_id": CHANNEL_ID, "photo": data['image'], "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code != 200:
        # Fallback for complex names breaking Markdown
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    
    save_cid(data['cid'])
    print(f"Posted: {data['name']}")

if __name__ == "__main__":
    used = get_used_cids()
    pool = get_cid_pool()
    random.shuffle(pool)
    
    print(f"Pool loaded. Checking candidates...")
    found = False
    # Check up to 500 candidates. This will definitely find a winner in seconds.
    for cid in pool[:500]:
        if str(cid) in used: continue
        data = get_molecule_details(cid)
        if data:
            post_to_telegram(data)
            found = True
            break
            
    if not found:
        print("Failure: Could not find molecule.")
        sys.exit(1)
