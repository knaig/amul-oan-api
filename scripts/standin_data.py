"""Seed data for the Beckn / PashuGPT stand-in. Real-shaped, deterministic.

Three farmers cover the planner's branches: single account with cows only
(Kaira), two accounts across species (Banas: union schemes + operated visits),
and a union banned from AI-call booking (Kutch/Sarhad).
"""
from __future__ import annotations

import hashlib
import random
import re
from datetime import date, timedelta

FARMERS: dict[str, list[dict]] = {
    "9876543210": [
        {
            "union_name": "Kaira", "union_code": "0201", "society_name": "Anand Dudh Utpadak Mandali", "society_gujarati_name": "આણંદ દૂધ ઉત્પાદક મંડળી",
            "society_code": "001066", "farmer_name": "Rameshbhai Patel", "farmer_gujarati_name": "રમેશભાઈ પટેલ", "farmer_code": "000123",
            "state": "Gujarat", "district": "Anand", "sub_district": "Anand", "village": "Vadod",
            "total_animals": "3", "total_cows": "3", "total_buffaloes": "0", "total_milking_animals": "2",
            "average_milk_cow": "18.5", "cow_snf": "8.6", "cow_fat": "4.1",
            "tags": ["GJ0201A001", "GJ0201A002", "GJ0201A003"],
            "technicians": [
                {"id": "AIT-1101", "name": "Kiran Patel", "gu": "કિરણ પટેલ", "mobile": "9825011111"},
                {"id": "AIT-1102", "name": "Meena Shah", "gu": "મીના શાહ", "mobile": "9825022222"},
            ],
        }
    ],
    "9898989898": [
        {
            "union_name": "Banas", "union_code": "0301", "society_name": "Deesa Dudh Mandali", "society_code": "004411",
            "farmer_name": "Hansaben Chaudhary", "farmer_gujarati_name": "હંસાબેન ચૌધરી", "farmer_code": "000778",
            "state": "Gujarat", "district": "Banaskantha", "sub_district": "Deesa", "village": "Bhildi",
            "total_animals": "4", "total_cows": "0", "total_buffaloes": "4", "total_milking_animals": "3",
            "average_milk_buffalo": "9.2", "buffalo_snf": "9.1", "buffalo_fat": "7.2",
            "tags": ["GJ0301B101", "GJ0301B102"],
            "technicians": [{"id": "AIT-3301", "name": "Vipul Desai", "gu": "વિપુલ દેસાઈ", "mobile": "9925033333"}],
        },
        {
            "union_name": "Banas", "union_code": "0301", "society_name": "Bhildi Dudh Mandali", "society_code": "004412",
            "farmer_name": "Hansaben Chaudhary", "farmer_code": "000779",
            "state": "Gujarat", "district": "Banaskantha", "sub_district": "Deesa", "village": "Bhildi",
            "total_animals": "2", "total_cows": "2", "total_buffaloes": "0", "total_milking_animals": "1",
            "average_milk_cow": "12.0", "cow_snf": "8.4", "cow_fat": "3.9",
            "tags": ["GJ0301B201"],
            "technicians": [{"id": "AIT-3301", "name": "Vipul Desai", "gu": "વિપુલ દેસાઈ", "mobile": "9925033333"}],
        },
    ],
    "9700000001": [
        {
            "union_name": "Sarhad", "union_code": "0901", "society_name": "Bhuj Dudh Mandali", "society_code": "002201",
            "farmer_name": "Karsanbhai Ahir", "farmer_code": "000455",
            "state": "Gujarat", "district": "Kutch", "sub_district": "Bhuj", "village": "Madhapar",
            "total_animals": "5", "total_cows": "2", "total_buffaloes": "3", "total_milking_animals": "3",
            "tags": ["GJ0901K001"],
            "technicians": [],
        }
    ],
}

ANIMALS: dict[str, dict] = {
    "GJ0201A001": {"animal_type": "Cow", "animal_name": "Gauri", "breed": "HF Cross", "milking_stage": "Milking", "pregnancy_stage": "Not Pregnant", "date_of_birth": "2021-03-14", "lactation_number": "3",
                   "last_breeding_activity": '{"type":"AI","date":"2026-07-02","result":"pending PD"}', "last_health_activity": '{"type":"Deworming","date":"2026-05-11"}'},
    "GJ0201A002": {"animal_type": "Cow", "animal_name": "Lakshmi", "breed": "Gir", "milking_stage": "Milking", "pregnancy_stage": "Pregnant", "date_of_birth": "2020-11-02", "lactation_number": "4"},
    "GJ0201A003": {"animal_type": "Cow", "animal_name": "Heifer", "breed": "HF Cross", "milking_stage": "Heifer", "pregnancy_stage": "Not Pregnant", "date_of_birth": "2025-01-20", "lactation_number": "0"},
    "GJ0301B101": {"animal_type": "Buffalo", "animal_name": "Kali", "breed": "Mehsani", "milking_stage": "Milking", "pregnancy_stage": "Not Pregnant", "date_of_birth": "2019-08-30", "lactation_number": "5"},
    "GJ0301B102": {"animal_type": "Buffalo", "animal_name": "Rani", "breed": "Banni", "milking_stage": "Dry", "pregnancy_stage": "Pregnant", "date_of_birth": "2018-02-12", "lactation_number": "6"},
    "GJ0301B201": {"animal_type": "Cow", "breed": "Kankrej", "milking_stage": "Milking", "pregnancy_stage": "Not Pregnant", "date_of_birth": "2022-06-06", "lactation_number": "2"},
    "GJ0901K001": {"animal_type": "Buffalo", "breed": "Banni", "milking_stage": "Milking", "pregnancy_stage": "Not Pregnant", "date_of_birth": "2020-01-15", "lactation_number": "3"},
}

BANAS_VISITS: dict[str, list[dict]] = {
    "GJ0301B101": [{"visit_code": "V-77012", "visit_date": "2026-08-14", "scheduled_date": "2026-08-14", "species": "Buffalo", "gender": "Female", "pregnancy_status": "Open", "breed": "Mehsani",
                    "milk_status": "Milking", "ailment_1": "Mastitis (clinical)", "disease": "Mastitis", "disease_group": "Udder", "prognosis": "Good", "visit_status": "Closed",
                    "medicines": [{"medicine_name": "Enrofloxacin 10%", "stock": "20", "unit": "ml"}, {"medicine_name": "Meloxicam", "stock": "15", "unit": "ml"}]}],
}

BONUS: dict[tuple[str, str, str], list[dict]] = {
    ("0201", "001066", "000123"): [{"societyCode": "001066", "societyName": "Anand Dudh Utpadak Mandali", "farmerCode": "000123", "farmerName": "Rameshbhai Patel", "bonusAmount": 4830.50, "fromDate": "2025-04-01T00:00:00", "toDate": "2026-03-31T00:00:00"}],
    ("0301", "004411", "000778"): [{"societyCode": "004411", "societyName": "Deesa Dudh Mandali", "farmerCode": "000778", "farmerName": "Hansaben Chaudhary", "bonusAmount": 7120.00, "fromDate": "2025-04-01T00:00:00", "toDate": "2026-03-31T00:00:00"}],
    ("0301", "004412", "000779"): [{"societyCode": "004412", "societyName": "Bhildi Dudh Mandali", "farmerCode": "000779", "farmerName": "Hansaben Chaudhary", "bonusAmount": 1950.25, "fromDate": "2025-04-01T00:00:00", "toDate": "2026-03-31T00:00:00"}],
}

SHC_CYCLES: dict[str, dict[str, str]] = {
    "9876543210": {"2024-25": """<!doctype html><html><body>
<h1>Soil Health Card</h1>
<div>Farmer Name</div><div>Rameshbhai Patel</div>
<div>Plot Address</div><div>Survey 112, Vadod, Anand</div>
<div>Soil Type</div><div>: Medium Black</div>
<h2>Soil Sample Details</h2>
<div>Available Nitrogen</div><span>(N)</span><b>212.40</b><span>kg/ha</span><span>Range :</span><span>280 - 560</span>
<div>Available Phosphorus</div><span>(P)</span><b>31.20</b><span>kg/ha</span><span>Range :</span><span>10 - 25</span>
<div>Available Potassium</div><span>(K)</span><b>318.00</b><span>kg/ha</span><span>Range :</span><span>108 - 280</span>
<div>pH</div><span>(pH)</span><b>7.9</b><span>Range :</span><span>5.5 - 8.5</span>
<div>EC</div><span>(EC)</span><b>0.42</b><span>dS/m</span><span>Range :</span><span>0 - 1</span>
<div>Organic Carbon</div><span>(OC)</span><b>0.48</b><span>%</span><span>Range :</span><span>0.5 - 0.75</span>
<div>Available Zinc</div><span>(Zn)</span><b>0.55</b><span>ppm</span><span>Range :</span><span>0.6 - 1.2</span>
<h2>MEASURED SCALE</h2>
<h2>Recommendation</h2>
<table><tr><th>Crop</th><th>Fertilizer Combination-1</th></tr>
<tr><td>Wheat</td><td>Urea 110 kg/ha, DAP 60 kg/ha, MOP 0 kg/ha, Zinc sulphate 25 kg/ha</td></tr>
<tr><td>Cotton</td><td>Urea 130 kg/ha, SSP 150 kg/ha, Zinc sulphate 25 kg/ha</td></tr></table>
</body></html>"""},
}

UNION_SCHEMES: dict[str, list[dict]] = {
    "banas": [
        {"title": "Pashu Accident Insurance (Pashu Vima)", "desc": "Banas Dairy covers member milk producers' cattle and buffaloes against accidental death. Premium is shared 50% by the union; claim within 7 days with the tag number and vet certificate.", "category": "insurance", "source": "banasdairy.coop/for-our-milk-producers"},
        {"title": "Cattle Feed Subsidy", "desc": "Rs 1 per kg subsidy on Banas Dan cattle feed purchased through the village society, credited in the milk bill.", "category": "input-subsidy", "source": "banasdairy.coop"},
        {"title": "Producer Accidental Death Benefit", "desc": "Rs 2 lakh to the family of a pouring member who dies in an accident; society passes the claim to the union.", "category": "welfare", "source": "banasdairy.coop"},
        {"title": "Bulk Milk Cooler Support", "desc": "Union funds 60% of a village society's bulk milk cooler; society contributes the rest from bonus retention.", "category": "infrastructure", "source": "banasdairy.coop"},
        {"title": "Free Veterinary Emergency Visit", "desc": "One free emergency vet visit per animal per year for pouring members; book through the society or the Amul AI helpline.", "category": "veterinary", "source": "banasdairy.coop"},
    ],
    "kaira": [
        {"title": "Amul Cattle Insurance Scheme", "desc": "Kaira union members can insure milch animals at a subsidised premium of Rs 350 per animal per year.", "category": "insurance", "source": "amul.coop"},
        {"title": "Mineral Mixture Subsidy", "desc": "20% subsidy on Amul mineral mixture bought through the society.", "category": "input-subsidy", "source": "amul.coop"},
    ],
    "sabar": [{"title": "Sabar Pashu Kalyan Yojana", "desc": "Free deworming and vaccination camps twice a year for member animals.", "category": "veterinary", "source": "sabardairy.org"}],
    "sumul": [{"title": "Sumul Cattle Shed Assistance", "desc": "Rs 25,000 assistance for building a scientific cattle shed for members pouring over 5 years.", "category": "infrastructure", "source": "sumul.com"}],
    "surendranagar": [{"title": "Sursagar Fodder Seed Subsidy", "desc": "50% subsidy on green fodder seed (jowar, maize, lucerne) for members.", "category": "input-subsidy", "source": "sursagardairy.com"}],
}

CENTRAL_SCHEMES: dict[str, dict] = {
    "kcc": {"name": "Kisan Credit Card", "desc": "Short-term credit for crop cultivation, animal husbandry and fisheries at 7% interest with 3% prompt-repayment subvention (effective 4%). Limit based on scale of finance; dairy farmers eligible for working capital up to Rs 3 lakh without collateral. Apply at any bank branch or CSC with Aadhaar, land/animal records and passbook.", "category": "credit"},
    "pmkisan": {"name": "PM-KISAN", "desc": "Rs 6,000 per year in three instalments of Rs 2,000 to land-holding farmer families. Registration on pmkisan.gov.in or at CSC with Aadhaar, land record and bank account. e-KYC mandatory.", "category": "income-support"},
    "pmfby": {"name": "Pradhan Mantri Fasal Bima Yojana", "desc": "Crop insurance at 2% premium for kharif, 1.5% for rabi, 5% for horticulture. Covers yield loss from natural calamities, pests and disease. Enrol through bank, CSC or the PMFBY portal before the cut-off date for the season.", "category": "insurance"},
    "shc": {"name": "Soil Health Card", "desc": "Free soil testing every 2 years with crop-wise fertiliser recommendations. Sample collected by the agriculture department; card available on soilhealth.dac.gov.in with the registered mobile.", "category": "soil"},
    "pmksy": {"name": "Pradhan Mantri Krishi Sinchayee Yojana", "desc": "Irrigation coverage: per-drop-more-crop micro irrigation subsidy 55% for small and marginal farmers, 45% for others.", "category": "irrigation"},
    "smam": {"name": "Sub-Mission on Agricultural Mechanization", "desc": "40-50% subsidy on tractors, power tillers, chaff cutters and other machinery through the state agriculture portal (ikhedut in Gujarat).", "category": "mechanisation"},
    "pdmc": {"name": "Per Drop More Crop", "desc": "Drip and sprinkler subsidy component of PMKSY, 55% for small and marginal farmers.", "category": "irrigation"},
    "nbhm": {"name": "National Beekeeping and Honey Mission", "desc": "Support for bee boxes, colonies and honey processing units.", "category": "allied"},
}
for _c in ("sathi", "pmasha", "aif", "pkvy", "nfsm", "rad", "ffs"):
    CENTRAL_SCHEMES.setdefault(_c, {"name": _c.upper(), "desc": f"Central scheme {_c}: eligibility and benefits as notified by the Ministry of Agriculture.", "category": "central"})

# Vet knowledge base: real-shaped advisory snippets (the corpus the Beckn vet leg
# would return from the Marqo index). Keep each item self-contained.
VET_KB: list[dict] = [
    {"name": "Mastitis in dairy cattle: signs and first response", "source": "Amul Animal Husbandry Handbook", "text": "Mastitis shows as swollen, hot, painful quarter, clots or watery milk, drop in yield and sometimes fever. Strip out the affected quarter every 2 to 3 hours, apply cold water in acute cases, keep the udder dry, and call the veterinarian for intramammary antibiotic and anti-inflammatory treatment. Do not pool milk from the affected quarter. Use California Mastitis Test at the society for early detection."},
    {"name": "Fever and loss of appetite in cows: what to check", "source": "GVK Veterinary Field Guide", "text": "A cow with fever above 39.5 C that stops eating may have FMD, HS, mastitis, metritis after calving, or tick fever (theileriosis, babesiosis). Check for ticks, mouth blisters, lameness, discoloured urine and udder changes. Provide clean water and shade, do not give home remedies in the mouth, and arrange a vet visit the same day; fever over 41 C or recumbency is an emergency."},
    {"name": "Milk fever (hypocalcemia) after calving", "source": "NDDB Dairy Knowledge Portal", "text": "Milk fever occurs within 72 hours of calving in high-yielding cows and buffaloes: staggering, cold ears, S-shaped neck, then recumbency. It is an emergency treated with intravenous calcium borogluconate by a veterinarian. Prevent with low-calcium diet in the last 3 weeks before calving and anionic salts; give oral calcium gel at calving to at-risk animals."},
    {"name": "Foot and mouth disease: vaccination and care", "source": "ICAR-IVRI Advisory", "text": "FMD causes blisters on tongue, gums, teats and between hooves, drooling, lameness and sharp drop in milk. Vaccinate every 6 months under the national programme (free at the society camps). Isolate sick animals, wash lesions with 1% potassium permanganate, apply glycerine or antiseptic ointment, feed soft food, and get vet treatment for secondary infection."},
    {"name": "Deworming schedule for cattle and buffaloes", "source": "Amul Animal Husbandry Handbook", "text": "Deworm calves at 10 days, then monthly till 6 months, then every 3 months. Adults twice a year (before and after monsoon) with albendazole or fenbendazole at label dose by weight; alternate drug classes to avoid resistance. Do not deworm in the first 3 months of pregnancy without vet advice. Milk withdrawal per label."},
    {"name": "Heat detection and the right time for artificial insemination", "source": "NDDB Dairy Knowledge Portal", "text": "Signs of heat: bellowing, restlessness, clear mucus discharge, standing to be mounted, swollen vulva. Heat lasts 12 to 18 hours in cows, shorter and often silent in buffaloes (check at night and early morning). Inseminate 12 hours after first standing heat is seen: morning heat, evening AI; evening heat, next morning AI. Book the AI technician through the society."},
    {"name": "Repeat breeding (uthla) in cows and buffaloes", "source": "GVK Veterinary Field Guide", "text": "A repeat breeder returns to heat after 3 or more inseminations. Causes: poor heat timing, uterine infection, mineral deficiency (phosphorus, copper), poor semen handling. Get a vet check for endometritis, feed 50 g mineral mixture daily, correct body condition, and record heat dates for accurate AI timing."},
    {"name": "Calf scours (diarrhoea): rehydration and prevention", "source": "ICAR-IVRI Advisory", "text": "Calf diarrhoea in the first month kills by dehydration. Give oral rehydration solution 2 litres per 10 kg body weight per day in small feeds between milk feeds, keep the calf warm and dry, continue milk. Colostrum within 2 hours of birth is the best prevention. Blood in dung, sunken eyes or inability to stand need a vet urgently."},
    {"name": "Green fodder requirement for a dairy cow", "source": "Amul Animal Husbandry Handbook", "text": "A 400 kg cow giving 10 litres needs about 25 to 30 kg green fodder, 5 to 6 kg dry fodder and 4 to 5 kg compound cattle feed daily, plus 50 g mineral mixture and 30 g salt. Increase feed by 400 g for every extra litre of milk. Give clean water at least 60 to 80 litres a day, more in summer."},
    {"name": "Bypass fat and bypass protein for high yielders", "source": "NDDB Dairy Knowledge Portal", "text": "Bypass fat (rumen-protected fat) supplies energy without disturbing rumen fermentation: feed 100 to 200 g per day to cows and buffaloes yielding over 10 litres from calving to peak. Bypass protein improves milk protein. Both are available as Amul or NDDB products through the society."},
    {"name": "Silage making from maize or jowar", "source": "GVK Field Guide", "text": "Harvest maize at milk-dough stage (65 to 70% moisture), chop to 2 cm, fill the pit or bag in layers, compact firmly to remove air, seal airtight with polythene and soil. Ready in 45 days; keeps 1 to 2 years if sealed. Sugarcane tops and napier can also be ensiled with 1 to 2% molasses."},
    {"name": "Summer heat stress management in dairy animals", "source": "Amul Animal Husbandry Handbook", "text": "In heat above 38 C milk drops 10 to 25%. Provide shade, sprinklers or wallowing for buffaloes, feed in cool hours (early morning and night), increase green fodder and water, add 30 g salt and electrolytes, and avoid transport at midday."},
    {"name": "Ticks and tick fever: control", "source": "ICAR-IVRI Advisory", "text": "Ticks cause anaemia and transmit theileriosis and babesiosis (high fever, pale eyes, coffee-coloured urine). Apply acaricide (deltamethrin or amitraz) as per label on animals and shed walls every 2 to 3 weeks in the season. Tick fever needs immediate vet treatment; vaccinate crossbred calves against theileriosis."},
    {"name": "Buying and selling cattle: Amul Pashudhan cattle trade facility", "source": "Amul Cattle Trade", "text": "Amul Pashudhan lets member farmers list a cow or buffalo for sale with tag number, breed, lactation, milk per day and expected price, and search animals for sale nearby by distance, breed, price range and seller rating. Listings are verified through the society. Contact the seller via the app; payment and transport are arranged between farmer and buyer, with society witness recommended."},
    {"name": "Ear tags and animal identification", "source": "Amul Animal Husbandry Handbook", "text": "Every animal gets a 12-digit INAPH ear tag applied by the society or AI technician. The tag links to breeding, health and milk records. If a tag is lost, inform the society within 7 days; a duplicate is issued and the record continues."},
    {"name": "AI receipt and pregnancy diagnosis follow-up", "source": "Amul Animal Husbandry Handbook", "text": "After artificial insemination the technician issues a receipt with date, bull number and tag. Get pregnancy diagnosis done 60 to 90 days after AI by the vet. If the animal returns to heat after 18 to 24 days, inseminate again and inform the technician."},
    {"name": "Bloat (tympany) in cattle: emergency care", "source": "GVK Veterinary Field Guide", "text": "Bloat shows as a swollen left flank, distress and difficulty breathing, often after lush green fodder or grain overload. It is an emergency: keep the animal standing and walking, give 250 to 500 ml vegetable oil or a defoaming agent by mouth, and call the vet immediately for trocarisation if breathing is laboured."},
    {"name": "Castration age and method for bull calves", "source": "ICAR-IVRI Advisory", "text": "Castrate bull calves not meant for breeding at 6 to 9 months using a Burdizzo clamp or by a veterinarian. Earlier castration reduces growth; later castration is more stressful. Keep the site clean and watch for swelling for a week."},
    {"name": "Mineral mixture and salt in daily ration", "source": "NDDB Dairy Knowledge Portal", "text": "Feed 50 g area-specific mineral mixture and 30 g common salt daily to every adult animal, 25 g to calves. Deficiency shows as pica (eating soil, cloth), repeat breeding, weak calves and low fat. Mineral mixture is available at the society."},
    {"name": "Postpartum care of cows and buffaloes", "source": "Amul Animal Husbandry Handbook", "text": "After calving ensure placenta is expelled within 12 hours (retention needs a vet), give warm water with jaggery, feed gradually increasing concentrate over 2 weeks, watch for milk fever and metritis (foul discharge, fever). Do not feed the colostrum-rich milk to the society for 5 days."},
    {"name": "Lameness and foot rot", "source": "GVK Veterinary Field Guide", "text": "Lameness with swelling between the claws and foul smell is foot rot, common in wet sheds. Clean and trim the hoof, apply 5% copper sulphate footbath twice weekly, keep the floor dry, and get antibiotic treatment from the vet for fever or deep infection."},
    {"name": "Colostrum feeding for newborn calves", "source": "NDDB Dairy Knowledge Portal", "text": "Feed 2 litres of colostrum within 2 hours of birth and 4 litres in the first 12 hours; antibody absorption stops after 24 hours. Then milk at 10% of body weight daily in two feeds, calf starter from week 2, and clean water from day 3."},
    {"name": "Wheat cultivation: sowing and irrigation schedule for Gujarat", "source": "Anand Agricultural University", "text": "Sow wheat 10 to 25 November with 100 to 120 kg seed per hectare at 22.5 cm row spacing. Basal 50% N with full P and K; remaining N at first irrigation (21 days) and tillering. Critical irrigations at crown root initiation, tillering, jointing, flowering and grain filling."},
    {"name": "Cotton pink bollworm management", "source": "Anand Agricultural University", "text": "Install pheromone traps at 5 per hectare from 45 days after sowing, remove and destroy rosette flowers, avoid extending the crop beyond 150 days, and spray recommended insecticide only when 10% flowers or bolls show damage. Destroy crop residue after final picking."},
    {"name": "Groundnut tikka leaf spot and rust", "source": "Junagadh Agricultural University", "text": "Leaf spot shows dark circular spots with yellow halo from 30 days after sowing; rust shows orange pustules on the lower leaf surface. Spray mancozeb 0.2% or a triazole fungicide at first appearance, repeat after 15 days, and rotate with a non-legume crop."},
    {"name": "Veterinary dispensary and polyclinic services", "source": "Gujarat Animal Husbandry Department", "text": "Government veterinary dispensaries provide free treatment, vaccination, deworming and pregnancy diagnosis on working days; polyclinics at district level offer surgery and laboratory tests. Mobile veterinary units (1962 helpline) attend emergencies at the doorstep."},
    {"name": "Gujarati livestock terms: pet katha, hichki, udhras", "source": "Amul AI Glossary", "text": "In Gujarati farmer speech 'pet katha' means colic or gut pain in the animal, 'hichki' hiccup-like spasms often from indigestion, 'udhras' cough which may indicate lungworm or pneumonia, 'uthla' repeat breeding. Treat each as a livestock complaint and advise a vet check when it persists beyond a day."},
    {"name": "Milk quality: improving fat and SNF", "source": "Amul Animal Husbandry Handbook", "text": "Low fat: increase dry fodder and green fodder ratio, avoid sudden feed change, feed bypass fat. Low SNF: add mineral mixture and adequate protein (cottonseed cake, groundnut cake), ensure clean water. Milk the animal fully and at fixed times; test at the society's analyser."},
]

WEATHER_TEMPLATE = [
    ("Light rain", 6.0, 24.0, 31.0, 82, 14), ("Cloudy", 1.5, 24.5, 32.0, 78, 12), ("Moderate rain", 18.0, 23.5, 29.0, 88, 18),
    ("Partly cloudy", 0.0, 25.0, 33.5, 70, 10), ("Sunny", 0.0, 25.5, 34.0, 62, 9),
]

MARKETS_BY_TOWN = {
    "anand": ("Anand APMC", "Anand", "Gujarat"), "junagadh": ("Junagadh APMC", "Junagadh", "Gujarat"), "rajkot": ("Rajkot APMC", "Rajkot", "Gujarat"),
    "bhuj": ("Bhuj APMC", "Kachchh", "Gujarat"), "deesa": ("Deesa APMC", "Banaskantha", "Gujarat"), "palanpur": ("Abu Road APMC", "Sirohi", "Rajasthan"),
    "mehsana": ("Mehsana APMC", "Mehsana", "Gujarat"), "surat": ("Surat APMC", "Surat", "Gujarat"), "vadodara": ("Padra APMC", "Vadodara", "Gujarat"),
    "jetpur": ("Jetpur APMC", "Rajkot", "Gujarat"), "gondal": ("Gondal APMC", "Rajkot", "Gujarat"), "unjha": ("Unjha APMC", "Mehsana", "Gujarat"),
}
BASE_PRICE = {"onion": 1450, "potato": 1250, "tomato": 1800, "wheat": 2450, "cotton": 7200, "groundnut": 6350, "castor seed": 6100, "cummin seed(cumin seed)": 24500,
              "bajra(pearl millet/cumbu)": 2350, "maize": 2150, "garlic": 9800, "green chilli": 3400, "soyabean": 4350, "mustard": 5450, "banana": 1600, "mango": 4200}


def _seeded(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16))


def milk_rows(farmer_code: str, start: date, end: date) -> tuple[list[dict], list[dict]]:
    rng = _seeded("milk", farmer_code, start, end)
    milk, deduction = [], []
    d = start
    while d <= end:
        for shift in ("Morning", "Evening"):
            qty = round(rng.uniform(5.5, 11.5), 2)
            fat = round(rng.uniform(3.6, 7.4), 1)
            snf = round(rng.uniform(8.3, 9.3), 1)
            rate = 34 + (fat - 3.5) * 4.6
            milk.append({"date": d.isoformat(), "shift": shift, "qty": str(qty), "fat": str(fat), "snf": str(snf), "amount": str(round(qty * rate, 2))})
        if d.day in (1, 16):
            deduction.append({"date": d.isoformat(), "account_name": "Cattle Feed", "amount": str(round(rng.uniform(600, 1400), 2))})
        d += timedelta(days=1)
    return milk, deduction


def mandi_rows(commodity: str, town: str, from_date: date, to_date: date) -> list[dict]:
    key = commodity.strip().lower()
    base = BASE_PRICE.get(key)
    if base is None:  # the real BPP fuzzy-matches names: "Cotton (Kapas)" -> cotton
        head = re.split(r"[\s(]", key)[0]
        base = next((v for k, v in BASE_PRICE.items() if k.split()[0] == head), None)
    if base is None:
        return []
    market, district, state = MARKETS_BY_TOWN.get(town.lower(), MARKETS_BY_TOWN["anand"])
    rng = _seeded("mandi", key, market)
    rows = []
    d = to_date
    while d >= from_date and len(rows) < 10:
        if d.weekday() != 6:  # markets closed Sunday
            modal = int(base * rng.uniform(0.92, 1.08))
            rows.append({"date": d.strftime("%d-%m-%Y"), "market": market, "district": district, "state": state, "modal": modal,
                         "min": int(modal * 0.9), "max": int(modal * 1.1), "variety": "Other", "grade": "FAQ", "name": commodity})
        d -= timedelta(days=1)
    return rows
