"""Business control names observed in Hub's listing particulars form."""

LIFESTYLE_FIELDS = {
    "aquatic_activities": "Aquatic Activities",
    "casino_estate": "Casino Estate",
    "coastal_beach": "Coastal/Beach",
    "complex": "Complex",
    "country_club": "Country Club",
    "country_living": "Country Living",
    "cul_de_sac": "Cul-de-sac",
    "dual_living": "Dual Living",
    "eco_estate": "Eco Estate",
    "equestrian_polo_estate": "Equestrian/Polo Estate",
    "estate": "Estate",
    "fishing_estate": "Fishing Estate",
    "game_stock_farm": "Game/StockFarm",
    "gated_community": "Gated Community",
    "golf_estate": "Golf Estate",
    "holiday_home": "Holiday Home",
    "holiday_resort": "Holiday Resort",
    "island_estate": "Island Estate",
    "lakefront": "Lakefront",
    "lifestyle_farm": "Lifestyle Farm",
    "marina": "Marina",
    "metropolitan": "Metropolitan",
    "mountain": "Mountain",
    "nature_reserve_lifestyle": "Nature Reserve",
    "retirement_village": "Retirement Village",
    "riverfrontage": "Riverfrontage",
    "security_complex": "Security Complex",
    "security_estate": "Security Estate",
    "student_accommodation": "Student Accommodation",
    "suburban": "Suburban",
    "university_college_community": "University/College Community",
    "waterfront": "Waterfront",
    "wellness_estate": "Wellnessestate",
    "wildlife_state": "Wildlife Estate",
    "winelands": "Winelands",
}

# These controls are absent from the DOM until General Property Features is
# expanded. A collapsed heading does not mean that no features are selected.
GENERAL_FIELDS = dict(line.split('|', 1) for line in '''has_access_gate|Access Gate
has_alarm|Alarm
has_borehole|Borehole
has_club_house|Clubhouse
has_electric_fencing|Electric Fencing
has_fence|Pool Fenced
has_garden|Garden
has_golf_course|Golf Course
has_jetty_berth|Jetty Berth
has_paving|Paving
are_pets_allowed|Pets Allowed
has_pool|Pool
has_scenic_view|Scenic View
has_sea_view|Sea View
has_security_post|Security Post
has_balcony|Balcony
has_braai_room|Braai room
has_deck|Deck
has_entrance_hall|Entrance Hall
has_family_tv_room|Family / TV Room
has_flatlet|Flatlet
has_domestic_rooms|Domestic Rooms
has_outside_toilets|Outside Toilets
has_second_house|Second House
has_guest_toilet|Guest Toilet
has_kitchen|Kitchen
has_lapa|Lapa
has_laundry|Laundry
has_pantry|Pantry
has_patio|Patio
reception|Reception
has_staff_quarters|Staff Quarters
has_storage|Storage
has_study|Study
has_24_hour_security|24 Hour Security
has_burglar_bars|Burglar Bars
has_security_gate|Security Gate
is_totally_fenced|Totally Fenced
is_partially_fenced|Partially Fenced
has_aircon|Aircon
has_cctv|CCTV
has_central_heating|Central Heating
has_fire_place|Fireplace
is_furnished|Furnished
has_gym|Gym
has_intercom|Intercom
has_irrigation_system|Irrigation System
has_satelite|Satellite
smoking_allowed|Smoking Allowed
has_spa_bath|Spa Bath
has_squash_court|Squash Court
is_wheelchair_friendly|Wheel Chair Friendly
has_adsl|ADSL
has_dialup|Dial-Up
has_fibre|Fibre
has_fixed_wi_max|Fixed Wi Max
has_isdn|ISDN
has_satellite|Satellite
has_vdsl|VDSL
has_awning|Awning
has_balcony_special_feature|Balcony
has_battery_inverter|Battery/Inverter
has_boat_launch|Boat Launch
has_built_in_bar|Built In Bar
has_built_in_braai|Built in Braai
has_central_vacuum_system|Central Vacuum System
has_communal_braai_area|Communal braai area
has_creche|Creche
has_driveway|Driveway
has_gas_geyser|Gas Geyser
has_jacuzzi|Jacuzzi
has_jetty|Jetty
has_lifts|Lifts
has_medical_care|Medical Care
has_open_plan_special_feature|Open Plan
has_paveway|Paveway
has_pressed_ceilings|Pressed Ceilings
has_satillite_dish|Satellite Dish
has_sauna|Sauna
has_sliding_doors|Sliding Doors
has_solar_geyser|Solar Geyser
has_solar_panels|Solar Panels
has_spa_pool|Spa Pool
has_special_doors|Special Doors
has_special_lights|Special Lights
has_sprinkler_system|Sprinkler System
has_subdivision_rights|Subdivision Rights
has_tennis_court|Tennis Court
has_totally_walled|Totally Walled
has_tv_antenna|TV Antenna
has_wallpaper|Wallpaper
has_water_tanks|Water Tanks
has_wendy_house|Wendy House
has_wood_ceilings|Wood Ceilings'''.splitlines())
