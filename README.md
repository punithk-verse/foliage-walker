# foliage-walker

Give it a place. It finds the nearby named trail with the most tree cover, picks the best-weather day this week, works out when to leave before sunset, and has a **local open-weight LLM** write a short invitation. It exports a GPX file so you can load the route into an offline map and put your phone away.

Built for the Hacktoberfest Open-Source AI Challenge, Week 1: Touch Grass.

## Open pieces
- **OpenStreetMap** (Nominatim + Overpass): trails, woods and parks
- **Open-Meteo**: elevation and 7-day forecast
- **Ollama + an open-weight model** (tested with `qwen2.5:1.5b`): writes the invitation. No API keys or accounts.

## Run it locally
```bash
ollama pull qwen2.5:1.5b
python foliage_walker.py "Agara Lake, Bengaluru" --radius 4 --min-km 1.5 --model qwen2.5:1.5b
```
Python 3 standard library only. Outputs `plan.md` and `route.gpx` (open in Organic Maps or OsmAnd).

## Run it on Google Colab
The author developed and tested it on a free Colab runtime because their laptop had under 600 MB of free RAM. See `foliage_walker.ipynb`, or:
```python
!apt-get install -y zstd > /dev/null
!curl -fsSL https://ollama.com/install.sh | sh
import subprocess, time
subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(6)
!ollama pull qwen2.5:1.5b
```
Then write `foliage_walker.py` into the notebook and run it as above.

## How it works
1. Nominatim geocodes the place.
2. Overpass returns named paths plus woods and parks. Each trail is scored by the share of its points that fall inside green polygons (point-in-polygon). Where broadleaf tags exist, it ranks by broadleaf cover instead.
3. Open-Meteo picks the day with the lowest rain chance, near 20 °C.
4. Code computes the leave-by time (sunset minus walking time at ~4 km/h, minus 30 min).
5. The LLM gets only trail, day and leave-by time and writes 3 sentences. All facts come from code, because in testing the model invented details when given freedom.

## Limitations
- Autumn colour timing is a rough latitude/elevation rule of thumb, not a forecast. It is skipped within 25° of the equator.
- OSM broadleaf tagging is patchy, so the tool often falls back to overall green cover. A trail inside one big mapped park can score 100%.
- Walking time is a flat 4 km/h assumption. The tool knows nothing about terrain, crowds or closures.
- The public Overpass server can time out; the script retries across mirrors.
- Tiny models ignore length limits, so the output is trimmed to 3 sentences.

## Example
See `examples/plan.md` and `examples/route.gpx`.

## License
MIT
