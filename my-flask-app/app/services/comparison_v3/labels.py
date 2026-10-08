# -*- coding: utf-8 -*-
"""Display labels of V3 (presentation only; never used to decide anything).

Brand names map the government manufacturer spelling (MILO ``tozar``) to the Latin brand name; an unknown
manufacturer is shown as the registry spells it.
"""

from __future__ import annotations

from typing import Any, Optional

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

# Source names for the attribution footer, by TRIPY fact source.
SOURCE_NAMES_HE = {
    "government": "משרד התחבורה",
    "eea_co2_cars": "EEA",
    "tc_cvs": "Transport Canada",
    "epa_fueleconomy": "EPA",
    "nrcan_fuel_ratings": "NRCan",
    "ademe_car_labelling": "ADEME",
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
