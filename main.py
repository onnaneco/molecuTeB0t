import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
MAX_CID = 1000000  # Lowered slightly for higher probability of having descriptions

def is_bad_name(name):
    """Returns True if the name looks like a database ID or a complex IUPAC string."""
    if not name: return True
    # Known database prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "CAS-", "MFCD", "PUBCHEM", 
        "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-"
    ]
    name_upper = name.upper()
    
    # Check prefixes
    if any(name_upper.startswith(p) for p in bad_prefixes):
        return True
    
    # Check if name is just a number
    if name.isdigit():
        return True

    # Check if it's a long IUPAC name (heuristic: contains many numbers/brackets)
    # Most common names don't have more than 2-3 dashes/brackets
    if len(re.findall(r'[\[\]\(\)\-\,\d]', name)) > 10:
        return True

    return False

def get_description_and_source(cid):
    """Fetches description based on priority: DrugBank > NCIt > MeSH > CAMEO."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            return None, None
        
        data = response.json()
        sections = data.get('Record', {}).get('Section', [])
        
        # We need to find the "Description" section usually under 'Notes' or 'Calculated Properties'
        # But specifically, we look for the Information array.
        all_descriptions = []
        
        def find_descriptions(data_list):
            for item in data_list:
                if 'Information' in item:
                    for info in item['Information']:
                        if 'Value' in info and 'StringWithMarkup' in info['Value'][0]:
                            text = info['Value'][0]['StringWithMarkup'][0]['String']
                            source = info.get('Reference', 'Unknown')
                            all_descriptions.append({'text': text, 'source': source})
                if 'Section' in item:
                    find_descriptions(item['Section'])

        find_descriptions(sections)

        # Priority Map
        priority = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]
        
        for p_source in priority:
            for desc in all_descriptions:
                if p_source.lower() in desc['source'].lower():
                    # Check word count (max 150)
                    word_count = len(desc['text'].split())
                    if word_count <= 150:
                        return desc['text'], p_source
                    
        return None, None
    except Exception:
        return None, None

def get_molecule_data():
    print("Searching for a molecule meeting all criteria...")
    for attempt in range(50):  # Increase attempts because criteria are strict
        cid = random.randint(1, MAX_CID)
        
        # 1. Get Synonyms and Properties
        prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
        syn_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
        
        try:
            p_res = requests.get(prop_url).json()
            s_res = requests.get(syn_url).json()
            
            iupac_name = p_res['PropertyTable']['Properties'][0].get('IUPACName')
            all_syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not iupac_name or not all_syns:
                continue

            # 2. Filter Names
            # Primary name: first synonym that isn't "bad" and isn't the IUPAC name
            clean_syns = [s for s in all_syns if not is_bad_name(s) and s.lower() != iupac_name.lower()]
            
            if not clean_syns:
                continue
                
            primary_name = clean_syns[0]
            aka = clean_syns[1:4] # Get up to 3 more
            
            # 3. Get Description based on priority
            description, source = get_description_and_source(cid)
            
            if not description:
                continue
                
            return {
                "name": primary_name,
                "aka": aka,
                "iupac": iupac_name,
                "description": description,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }

        except Exception as e:
            continue
            
    print("Failed to find molecule after 50 tries.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN:
        print("Error: No Token found.")
        return

    # Formatting AKA
    aka_text = ", ".join(data['aka']) if data['aka'] else "N/A"
    
    # Escape Markdown V1 special characters (simple approach)
    def clean(text):
        return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[")

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
        print("Posted successfully!")
    else:
        print(f"Post failed: {r.text}")

if __name__ == "__main__":
    mol_data = get_molecule_data()
    post_to_telegram(mol_data)
