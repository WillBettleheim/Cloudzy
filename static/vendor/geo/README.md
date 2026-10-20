Geographic data for the Weather Explorer, built by tools/build_geo.py from Natural Earth v5.1.2
(https://github.com/nvkelso/natural-earth-vector/tree/v5.1.2). Natural Earth is in the public domain
(https://www.naturalearthdata.com/about/terms-of-use/). "Made with Natural Earth."

- countries.json: country outlines from ne_10m_admin_0_countries_ind (India's point of view: Natural Earth
  worldview "IND"), simplified to 0.04° and rounded to 0.01°; continent, subregion and label point from Natural
  Earth. Continent label points and radii are derived from the member countries (not a Natural Earth layer).
- admin1/<A3>.json: first-level divisions from ne_10m_admin_1_states_provinces (simplified to 0.01°) and the
  internal boundaries between them from ne_10m_admin_1_states_provinces_lines, for the 210 countries that have
  two or more divisions. Lines are kept only inside their own country's outline.
- borders.json: land borders between countries in the same point of view, for the 2D map: edges shared by two
  countries' unsimplified outlines (Natural Earth builds neighbours from shared vertices), chained into lines and
  simplified to 0.02°.
- index.json: names of countries and divisions for search (no geometry).
- places.json: ne_10m_populated_places_simple (7,342 cities and towns, with Natural Earth's scale rank).

Point-of-view adjustments: divisions whose label point lies inside another country's outline in this view are
left out (Pakistan's Azad Kashmir and Northern Areas, Russia's Crimea and Sevastopol). A division that reaches
into an area shown as another country is marked "clip" (HaZafon, Israel), and the globe trims it to its own
country. Natural Earth has no Indian state polygons for areas that are Indian in this view but not administered by
India; those areas sit inside India's outline without a state.

Corrections: India's division types follow the constitutional list (28 states, 8 union territories); Natural
Earth v5.1.2 labels Himachal Pradesh a union territory and gives Gujarat no type. "Andaman and Nicobar" is shown
as "Andaman and Nicobar Islands".

Coverage: first-level divisions exist for 210 of 239 countries; their completeness and currency are Natural
Earth's (checked for India: all 28 states and 8 union territories, including Ladakh and the merged Dadra and
Nagar Haveli and Daman and Diu). Small countries and territories may have no divisions.
