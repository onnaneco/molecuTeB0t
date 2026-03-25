import requests
import random
import sys
import os
import time
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
# Curated molecules (with descriptions) are usually in the lower CID range
MAX_CID = 1000000 

def is_bad_name(name):
    """Returns True if the name is a database ID or an overly complex IUPAC-like string."""
    if not name: return True
    
    # 1. Database ID Prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "CAS-", "MFCD", "PUBCHEM", 
        "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", 
        "SBB-", "BDBM", "GTPL", "STK", "USEEGE", "YIL"
    ]
    name_upper = name.upper()
    if any(name_upper.startswith(p) for p in bad_prefixes):
        return True
    
    # 2. Check if name is just a number or looks like a code (e.g., '123-45-6')
    if re.match(r'^[0-9\-]+$', name):
        return True

    # 3. Filter out IUPAC-like names (heuristic: lots of numbers, dashes, and brackets)
    # Common names like "Aspirin" or "Caffeine" have 0-1 dashes. 
    # IUPAC names like "N-(4-hydroxyphenyl)acetamide" have many.
    special_chars = len(re.findall(r'[\[\]\(\)\-\,\d]', name))
    if special_chars > 8: 
        return True

    return False

def get_description_and_source(cid):
    """
    Fetches description based on priority: DrugBank > NCIt > MeSH > CAMEO.
    Returns (DescriptionText, SourceName)
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return None, None
        
        data = response.json()
        
        # Priority mapping
        priority = {
            "DrugBank": 1,
            "NCI Thesaurus (NCIt)": 2,
            "Medical Subject Headings (MeSH)": 3,
            "CAMEO Chemicals": 4
        }
        
        found_descriptions = []

        def find_in_sections(sections):
            for sec in sections:
                # Look for Record Description sections
                if sec.get('TOCHeading') in ['Record Description', 'Description', 'Computed Properties']:
                    for info in sec.get('Information', []):
                        val = info.get('Value', {}).get('StringWithMarkup')
                        if val:
                            text = val[0].get('String')
                            # Check source
                            source = info.get('Reference', '')
                            for p_name, p_rank in priority.items():
                                if p_name.lower() in source.lower():
                                    found_descriptions.append({
                                        'text': text,
                                        'source': p_name,
                                        'rank': p_rank
                                    })
                if 'Section' in sec:
                    find_in_sections(sec['Section'])

        if 'Section' in data.get('Record', {}):
            find_in_sections(data['Record']['Section'])

        if not found_descriptions:
            return None, None

        # Sort by priority rank
        found_descriptions.sort(key=lambda x: x['rank'])
        
        # Pick the best one that meets word count
        for desc in found_descriptions:
            word_count = len(desc['text'].split())
            if 10 < word_count <= 150: # Ensure it's not too short or too long
                return desc['text'], desc['source']
                
        return None, None
    except:
        return None, None

def get_molecule_data():
    print("Searching for a molecule meeting all criteria...")
    attempts = 0
    
    while True: # Keep going until we find one
        attempts += 1
        cid = random.randint(1, MAX_CID)
        
        # To avoid being blocked, wait a tiny bit every few requests
        if attempts % 5 == 0:
            time.sleep(0.5)
            print(f"  ... Attempt {attempts} (Searching CID {cid})")

        try:
            # Get Names/Properties
            prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            syn_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_res = requests.get(prop_url, timeout=5).json()
            s_res = requests.get(syn_url, timeout=5).json()
            
            iupac_name = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            all_syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not iupac_name or not all_syns:
                continue

            # 1. Determine Primary Name (first name that isn't "bad")
            primary_name = None
            for s in all_syns:
                if not is_bad_name(s):
                    primary_name = s
                    break
            
            if not primary_name:
                continue

            # 2. Determine AKA (next 3 non-bad names, not same as primary)
            aka_list = []
            for s in all_syns:
                if s != primary_name and not is_bad_name(s) and s.lower() != iupac_name.lower():
                    aka_list.append(s)
                    if len(aka_list) >= 3:
                        break
            
            # 3. Get Description based on priority
            description, source = get_description_and_source(cid)
            
            if not description:
                continue
                
            # Success!
            print(f"Success! Found {primary_name} after {attempts} attempts.")
            return {
                "name": primary_name,
                "aka": aka_list,
                "iupac": iupac_name,
                "description": description,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }

        except Exception:
            continue

def post_to_telegram(data):
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN not found.")
        return

    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape simple Markdown characters to avoid parsing errors
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

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
    
    response = requests.post(api_url, data=payload)
    if response.status_code == 200:
        print("Successfully posted to Telegram!")
    else:
        print(f"Failed to post. Response: {response.text}")

if __name__ == "__main__":
    mol_data = get_molecule_data()
    post_to_telegram(mol_data)
