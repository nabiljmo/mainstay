"""Country registry: ISO3 code -> name and bounding box (lon_min, lat_min, lon_max, lat_max).

Bounding boxes are generous — they only crop the continental CHIRPS grid down to
a working window per country. Exact country shapes come from GADM later (zoning
approval and admin-snap), never from these boxes.
"""

COUNTRIES: dict[str, dict] = {
    "KEN": {"name": "Kenya", "bbox": (33.9, -4.8, 42.0, 5.6)},
    "NGA": {"name": "Nigeria", "bbox": (2.6, 4.2, 14.7, 13.9)},
    "TZA": {"name": "Tanzania", "bbox": (29.3, -11.8, 40.5, -0.9)},
    "UGA": {"name": "Uganda", "bbox": (29.5, -1.5, 35.0, 4.3)},
    "ZMB": {"name": "Zambia", "bbox": (21.9, -18.1, 33.7, -8.2)},
    "MWI": {"name": "Malawi", "bbox": (32.6, -17.2, 35.9, -9.3)},
    "RWA": {"name": "Rwanda", "bbox": (28.8, -2.9, 30.9, -1.0)},
    "ETH": {"name": "Ethiopia", "bbox": (32.9, 3.4, 48.0, 14.9)},
    "GHA": {"name": "Ghana", "bbox": (-3.3, 4.7, 1.2, 11.2)},
    "SEN": {"name": "Senegal", "bbox": (-17.6, 12.3, -11.3, 16.7)},
    "MOZ": {"name": "Mozambique", "bbox": (30.2, -26.9, 40.9, -10.4)},
    "ZWE": {"name": "Zimbabwe", "bbox": (25.2, -22.5, 33.1, -15.6)},
    "BFA": {"name": "Burkina Faso", "bbox": (-5.6, 9.4, 2.4, 15.1)},
    "MLI": {"name": "Mali", "bbox": (-12.3, 10.1, 4.3, 25.0)},
    "NER": {"name": "Niger", "bbox": (0.1, 11.6, 16.0, 23.5)},
}
