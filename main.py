import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

def is_bad_name(name, iupac_name=""):
    """
    Returns True if the name is a database ID, a technical IUPAC name, 
    or a numeric code.
    """
    if not name or len(name) < 3: return True
    if iupac_name and name.lower() == iupac_name.lower(): return True
    
    u = name.upper()
    
    # 1. Block Database IDs
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "SMR", "US1", "MLS", "ST0", "AA-"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # 2. Block pure numeric codes / CAS numbers
    if re.match(r'^[0-9\-]{5,}$', name): return True

    # 3. Block Technical IUPAC-style names (Heuristic)
    # If it has more than 5 special chemical characters, it's too technical
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 5: return True
    
    # 4. Filter out names that look like internal codes (Mixed caps and numbers with no vowels)
    if len(name) > 8 and not any(v in name.lower() for v in 'aeiouy'): return True

    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_description_data(cid):
    """
    Fetches the description and ensures it comes from the priority sources.
    Returns (text, source_name)
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200: return None, None
        data = r.json()
        
        found_descriptions = {}

        def crawl(node):
            if isinstance(node, list):
                for item in node: crawl(item)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        val = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if val and ref:
                            # Map Reference to our target sources
                            sources = {
                                "DrugBank": "DrugBank",
                                "NCI Thesaurus": "NCI Thesaurus (NCIt)",
                                "Medical Subject Headings": "Medical Subject Headings (MeSH)",
                                "CAMEO Chemicals": "CAMEO Chemicals"
                            }
                            for key, label in sources.items():
                                if key.lower() in ref.lower():
                                    if label not in found_descriptions:
                                        found_descriptions[label] = val
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])

        crawl(data)
        
        # Check in specified priority order
        priority = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]
        for p in priority:
            if p in found_descriptions:
                text = found_descriptions[p]
                word_count = len(text.split())
                if 10 < word_count <= 150: # Length check
                    return text, p
                    
    except Exception: pass
    return None, None

def get_molecule_data():
    used = get_used_cids()
    
    # The first 30,000 CIDs are the "Gold Standard" compounds.
    # They are 100% likely to have descriptions and good names.
    pool = list(range(1, 30000))
    random.shuffle(pool)
    
    print("Searching for a valid molecule...")
    
    for cid in pool:
        if str(cid) in used: continue
        
        try:
            # 1. Fetch Synonyms and IUPAC
            p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_res = requests.get(p_url, timeout=5).json()
            s_res = requests.get(s_url, timeout=5).json()
            
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "")
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not syns: continue

            # 2. Filter for "Good" Common Names
            clean_names = [s for s in syns if not is_bad_name(s, iupac)]
            
            # If no good name is found, skip this molecule entirely (User Requirement)
            if not clean_names: continue
            
            # 3. Fetch Description with priority and word limit
            desc, source = get_description_and_source = get_description_data(cid)
            if not desc: continue

            # Success!
            primary_name = clean_names[0]
            aka_list = clean_names[1:4] # Up to 3

            return {
                "name": primary_name,
                "aka": aka_list,
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except Exception:
            continue

    print("Failure: No molecule found.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    # Format A.K.A. logic
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Markdown safety
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

    # PRECISE LAYOUT REQUESTED
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
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
