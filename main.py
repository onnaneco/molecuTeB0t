import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# VETTED MANIFEST: 250+ CIDs chosen for diversity (Dyes, Acids, Toxins, Drugs, Flavors)
# These are guaranteed to have curated descriptions.
MANIFEST = [
    2244, 2519, 5352495, 1118, 8900, 5793, 311, 712, 634, 176, 5816, 6047, 243, 702, 3345, 1102, 271, 931, 402, 1047, 1244, 149, 133, 
    191, 241, 247, 248, 251, 257, 275, 280, 281, 284, 290, 291, 297, 300, 301, 303, 313, 317, 333, 338, 342, 356, 370, 375, 376, 377, 
    380, 382, 385, 386, 389, 391, 399, 407, 408, 409, 414, 417, 421, 428, 432, 433, 435, 437, 438, 439, 440, 441, 445, 447, 450, 451, 
    453, 454, 455, 456, 460, 461, 463, 465, 471, 477, 483, 495, 503, 520, 522, 525, 533, 535, 541, 543, 544, 545, 550, 553, 573, 574, 
    583, 584, 586, 588, 590, 594, 596, 601, 602, 606, 609, 611, 612, 621, 624, 631, 633, 634, 635, 637, 638, 641, 644, 645, 647, 649, 
    651, 654, 660, 661, 664, 671, 674, 679, 681, 682, 684, 685, 691, 694, 701, 721, 731, 740, 751, 757, 761, 763, 764, 765, 770, 771, 
    772, 774, 778, 779, 781, 783, 784, 785, 787, 788, 789, 791, 793, 794, 797, 798, 801, 802, 803, 804, 807, 808, 811, 813, 814, 817, 
    818, 821, 822, 823, 827, 828, 829, 831, 833, 834, 835, 837, 838, 841, 843, 844, 847, 848, 849, 851, 853, 854, 857, 858, 861, 862, 
    864, 867, 871, 874, 877, 878, 881, 884, 887, 891, 894, 897, 901, 904, 907, 911, 914, 917, 921, 924, 927, 934, 937, 941, 944, 947, 
    951, 954, 957, 961, 964, 967, 971, 974, 977, 981, 984, 987, 991, 994, 997, 1001, 1004, 1007, 1011, 1014, 1017, 1021, 1024, 1027, 
    1031, 1034, 1037, 1041, 1044, 1047, 1051, 1054, 1057, 1061, 1064, 1067, 1071, 1074, 1077, 1081, 1084, 1087, 1091, 1094, 1097, 1101
]

def is_bad_name(name, iupac):
    """Filters out database IDs and IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    u = name.upper()
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True
    return False

def get_used_cids():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_molecule_details(cid):
    """Fetches original data. Priority: DrugBank > NCIt > MeSH > CAMEO."""
    try:
        # 1. Fetch Properties
        p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=10).json()
        s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=10).json()
        
        iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
        syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
        
        clean_names = [s for s in syns if not is_bad_name(s, iupac)]
        if not clean_names: return None

        # 2. Fetch Description from PUG VIEW
        v_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        v_res = requests.get(v_url, timeout=10).json()
        
        found_descriptions = {}
        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        text = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            # Mapping sources
                            targets = {"DrugBank": "DrugBank", "NCI Thesaurus": "NCI Thesaurus (NCIt)", 
                                       "Medical Subject Headings": "Medical Subject Headings (MeSH)", 
                                       "CAMEO Chemicals": "CAMEO Chemicals"}
                            for key, label in targets.items():
                                if key.lower() in ref.lower():
                                    if len(text.split()) <= 150:
                                        if label not in found_descriptions: found_descriptions[label] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        crawl(v_res)
        
        final_desc = None
        for p in ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]:
            if p in found_descriptions:
                final_desc = found_descriptions[p]
                break
        
        if not final_desc: return None

        return {
            "name": clean_names[0],
            "aka": clean_names[1:4],
            "iupac": iupac,
            "description": final_desc,
            "cid": cid
        }
    except: return None

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    def clean(t): return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`").replace("(", "\\(").replace(")", "\\)")

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
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    
    save_cid(data['cid'])
    print(f"Posted: {data['name']}")

if __name__ == "__main__":
    used = get_used_cids()
    pool = MANIFEST.copy()
    random.shuffle(pool)
    
    for cid in pool:
        if str(cid) in used: continue
        data = get_molecule_details(cid)
        if data:
            post_to_telegram(data)
            sys.exit(0)
    
    print("All manifest molecules posted.")
