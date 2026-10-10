# -*- coding: utf-8 -*-
"""Display labels of V3 (presentation only; never used to decide anything).

Brand names map the government manufacturer spelling (MILO ``tozar``) to the Latin brand name; an unknown
manufacturer is shown as the registry spells it.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.comparison_v3.contracts import GOVERNMENT_DATASET_LABEL_HE

BRAND_DISPLAY = {
    "מרצדס": "Mercedes-Benz", "מרצדס בנץ": "Mercedes-Benz", "ב מ וו": "BMW", "טויוטה": "Toyota", "אאודי": "Audi",
    "אודי": "Audi", "סקודה": "Skoda", "פולקסווגן": "Volkswagen", "יונדאי": "Hyundai", "וולבו": "Volvo",
    "פיג'ו": "Peugeot", "קיה": "Kia", "טסלה": "Tesla", "פורשה": "Porsche", "שברולט": "Chevrolet", "רובר": "Rover",
    "פורד": "Ford", "סיאט": "Seat", "רנו": "Renault", "סיטרואן": "Citroen", "סובארו": "Subaru", "ניסאן": "Nissan",
    "אופל": "Opel", "לנדרובר": "Land Rover", "קרייזלר": "Chrysler", "הונדה": "Honda", "פיאט": "Fiat", "מזדה": "Mazda",
    "ג'יפ": "Jeep", "מיצובישי": "Mitsubishi", "לקסוס": "Lexus", "סוזוקי": "Suzuki", "אלפא רומיאו": "Alfa Romeo",
    "יגואר": "Jaguar", "קאדילאק": "Cadillac", "דאצ'יה": "Dacia", "דאציה": "Dacia", "סמארט": "Smart",
    "מזארטי": "Maserati", "מ.ג": "MG", "די אס": "DS", "צ'רי": "Chery", "בנטלי": "Bentley", "ג'י.אמ.סי": "GMC",
    "פרארי": "Ferrari", "בי ווי די": "BYD", "סאנגיונג": "SsangYong", "דודג'": "Dodge", "אקספנג": "XPENG",
    "אסטון מרטין": "Aston Martin", "ביואיק": "Buick", "קופרה": "Cupra", "גילי": "Geely", "למבורגיני": "Lamborghini",
    "זיקר": "Zeekr", "ליפמוטור": "Leapmotor", "לינק אנד קו": "Lynk & Co", "רולס רויס": "Rolls-Royce",
    "פולסטאר": "Polestar", "גרייט וול": "Great Wall",
}

FUEL_LABELS_HE = {"petrol": "בנזין", "diesel": "דיזל", "electric": "חשמלי", "plug_in_hybrid": "פלאג-אין", "lpg": "גפ״מ"}
PROPULSION_LABELS_HE = {"conventional": "מנוע בעירה", "hybrid": "היברידי", "plug_in": "פלאג-אין",
                        "plug_in_hybrid": "פלאג-אין", "battery_electric": "חשמלי"}
DRIVETRAIN_LABELS_HE = {"awd": "הנעה כפולה (AWD)", "four_wheel_drive": "הנעה כפולה (4X4)",
                        "two_wheel_drive": "הנעה על ציר אחד (2WD)"}
BODY_LABELS_HE = {"suv": "SUV", "sedan": "סדאן", "hatchback": "האצ'בק", "mpv": "רב-מושבי (MPV)", "coupe": "קופה",
                  "wagon": "סטיישן", "convertible": "גג נפתח", "pickup": "טנדר", "van": "מסחרית"}
GEARBOX_LABELS_HE = {"automatic": "אוטומטית", "manual": "ידנית", "dual_clutch": "כפולת מצמדים", "cvt": "רציפה (CVT)",
                     "e_cvt": "רציפה חשמלית (e-CVT)", "single_speed": "הילוך יחיד", "automated_manual": "רובוטית"}

# The 19 government driver-assistance indicators (MILO ``EQUIPMENT_INDICATORS`` order) and their Hebrew names, as in
# ``comparison_v2.level15`` (copied: the V3 pipeline does not import V2's registry modules).
ADAS_LABELS_HE = {
    "bakarat_mehirut_isa": "בקרת מהירות חכמה (ISA)",
    "bakarat_shyut_adaptivit_ind": "בקרת שיוט אדפטיבית",
    "bakarat_stiya_activ_s": "שמירה אקטיבית על נתיב",
    "bakarat_stiya_menativ_ind": "התרעת סטייה מנתיב",
    "blima_otomatit_nesia_leahor": "בלימה אוטומטית בנסיעה לאחור",
    "blimat_hirum_lifnei_holhei_regel_ofanaim": "בלימת חירום מול הולכי רגל ורוכבי אופניים",
    "hayshaney_hagorot_ind": "חיישני חגורות בטיחות",
    "hayshaney_lahatz_avir_batzmigim_ind": "חיישני לחץ אוויר בצמיגים",
    "hitnagshut_cad_shetah_met": "מניעת התנגשות צידית בשטח מת",
    "maarechet_ezer_labalam_ind": "מערכת עזר לבלימה",
    "matzlemat_reverse_ind": "מצלמת רוורס",
    "nitur_merhak_milfanim_ind": "ניטור מרחק מלפנים",
    "shlita_automatit_beorot_gvohim_ind": "שליטה אוטומטית באורות גבוהים",
    "teura_automatit_benesiya_kadima_ind": "תאורה אוטומטית בנסיעה קדימה",
    "zihuy_beshetah_nistar_ind": "זיהוי רכב בשטח מת",
    "zihuy_holchey_regel_ind": "זיהוי הולכי רגל",
    "zihuy_matzav_hitkarvut_mesukenet_ind": "זיהוי התקרבות מסוכנת",
    "zihuy_rechev_do_galgali": "זיהוי רכב דו-גלגלי",
    "zihuy_tamrurey_tnua_ind": "זיהוי תמרורי תנועה",
}
ADAS_ORDER = tuple(ADAS_LABELS_HE)


def adas_label(flag: str) -> str:
    return ADAS_LABELS_HE.get(flag, flag)


# Source names for the attribution footer, by TRIPY fact source.
SOURCE_NAMES_HE = {
    "government": "משרד התחבורה",
    "eea_co2_cars": "EEA",
    "tc_cvs": "Transport Canada",
    "epa_fueleconomy": "EPA",
    "nrcan_fuel_ratings": "NRCan",
    "ademe_car_labelling": "ADEME",
    # the ministry's data.gov.il datasets (source_level government_dataset): never labelled open data
    "gov_new_car_prices": GOVERNMENT_DATASET_LABEL_HE,
    "gov_recall_notices": GOVERNMENT_DATASET_LABEL_HE,
    "gov_road_survival": GOVERNMENT_DATASET_LABEL_HE,
    "user_supplied": None,           # the user's own asking price is not a data source
}


def brand_display(manufacturer: Optional[str]) -> str:
    name = (manufacturer or "").strip()
    return BRAND_DISPLAY.get(name, name)


def value_label(value: Any) -> str:
    """Hebrew label of a descriptive code value (as-is when unknown)."""
    if isinstance(value, bool):
        return "כן" if value else "לא"
    if isinstance(value, str):
        for table in (DRIVETRAIN_LABELS_HE, BODY_LABELS_HE, GEARBOX_LABELS_HE, PROPULSION_LABELS_HE, FUEL_LABELS_HE):
            if value in table:
                return table[value]
    return str(value)
