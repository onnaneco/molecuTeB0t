import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"

# Priority sources for descriptions
DESC_SOURCES = [
    {"name": "DrugBank", "label": "DrugBank"},
    {"name": "NCI Thesaurus", "label": "NCI Thesaurus (NCIt)"},
    {"name": "Medical Subject Headings", "label": "Medical Subject Headings (MeSH)"},
    {"name": "CAMEO Chemicals", "label": "CAMEO Chemicals"}
]

def is_bad_name(name):
    """Filters out database IDs and technical IUPAC-style names."""
    if not name: return True
    # Blacklist of database prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "CAS-", "MFCD", "PUBCHEM", 
        "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-"
    ]
    name_u = name.upper()
    if any(name_u.startswith(p) for p in bad_prefixes):
        return True
    
    # Heuristic: If name has too many numbers/brackets/dashes, it's likely a technical IUPAC string
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 7:
        return True
        
    return False

def get_description_and_source(cid):
    """Fetches PUG VIEW data and extracts description based on priority."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200: return None, None
        data = response.json()
        
        sections = data.get('Record', {}).get('Section', [])
        found_descriptions = {}

        def find_in_sections(node):
            for item in node:
                if 'Information' in item:
                    for info in item['Information']:
                        if 'Value' in info and 'StringWithMarkup' in info['Value'][0]:
                            text = info['Value'][0]['StringWithMarkup'][0]['String']
                            ref = info.get('Reference', '')
                            # Check if reference matches our target sources
                            for src in DESC_SOURCES:
                                if src['name'].lower() in ref.lower():
                                    found_descriptions[src['label']] = text
                if 'Section' in item:
                    find_in_sections(item['Section'])

        find_in_sections(sections)

        # Priority selection
        for src in DESC_SOURCES:
            text = found_descriptions.get(src['label'])
            if text:
                word_count = len(text.split())
                if word_count <= 150:
                    return text, src['label']
        return None, None
    except:
        return None, None

def get_molecule_data():
    print("Building CID pool from high-quality sources...")
    pool = []
    # Fetch CIDs from our 4 sources to ensure we have valid candidates
    for src in DESC_SOURCES:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/{src['name'].replace(' ', '%20')}/cids/JSON"
        try:
            r = requests.get(url, timeout=10).json()
            ids = r.get('IdentifierList', {}).get('CID', [])
            pool.extend(ids)
        except: continue
    
    if not pool:
        print("Error: Could not build CID pool.")
        sys.exit(1)

    random.shuffle(pool)
    
    print(f"Pool size: {len(pool)}. Searching for a candidate...")
    for cid in pool[:100]: # Check up to 100 random molecules from the quality pool
        # 1. Get Synonyms and IUPAC
        try:
            prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            syn_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_res = requests.get(prop_url, timeout=5).json()
            s_res = requests.get(syn_url, timeout=5).json()
            
            iupac = p_res['PropertyTable']['Properties'][0].get('IUPACName')
            all_syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not iupac or not all_syns: continue

            # 2. Filter Names
            # Primary name: first non-bad name that isn't the IUPAC name
            clean_syns = [s for s in all_syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            if not clean_syns: continue
            
            primary_name = clean_names[0] if 'clean_names' in locals() else clean_syns[0]
            aka = [s for s in clean_syns if s != primary_name][:3]

            # 3. Get Description
            desc, source_label = get_description_and_source(cid)
            if not desc: continue

            return {
                "name": primary_name,
                "aka": aka,
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except: continue

    print("Failed to find a suitable molecule in the pool.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Clean text for Markdown (escape underscores which are common in chem)
    def clean(t): return str(t).replace("_", "\\_").replace("*", "\\*")

    caption = (
        f"*Name:* {clean(data['name'])}\n"
        f"*A.K.A.:* {clean(aka_text)}\n"
        f"*IUPAC Name:* {clean(data['iupac'])}\n\n"
        f"*Description:* {clean(data['description'])}\n\n"
        f"*PubChem Link:* https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}"
    )

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHANNEL_ID,
        "photo": data['image'],
        "caption": caption,
        "parse_mode": "Markdown"
    }
    
    r = requests.post(api_url, data=payload)
    if r.status_code == 200:
        print(f"Posted: {data['name']}")
    else:
        print(f"Error: {r.text}")

if __name__ == "__main__":
    mol_data = get_molecule_data()
    post_to_telegram(mol_data)
