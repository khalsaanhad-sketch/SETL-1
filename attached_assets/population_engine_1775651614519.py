import rasterio
from rasterio.windows import from_bounds

dataset = rasterio.open("cloud_app/data/population.tif")

cache = {
    "bbox": None,
    "grid": None,
    "window": None
}


def load_population_window(min_lat, max_lat, min_lon, max_lon):
    window = from_bounds(
        min_lon, min_lat,
        max_lon, max_lat,
        dataset.transform
    )
    data = dataset.read(1, window=window)
    return data, window


def get_cached_population(lat, lon):
    bbox = (round(lat, 2), round(lon, 2))

    if cache["bbox"] != bbox:
        min_lat = lat - 0.05
        max_lat = lat + 0.05
        min_lon = lon - 0.05
        max_lon = lon + 0.05

        grid, window = load_population_window(min_lat, max_lat, min_lon, max_lon)

        cache["bbox"] = bbox
        cache["grid"] = grid
        cache["window"] = window

    return cache["grid"], cache["window"]


def sample_population(lat, lon, window, pop_grid):
    try:
        row, col = dataset.index(lon, lat)

        row -= int(window.row_off)
        col -= int(window.col_off)

        if 0 <= row < pop_grid.shape[0] and 0 <= col < pop_grid.shape[1]:
            return min(pop_grid[row, col] / 1000, 1)
    except:
        pass

    return 0