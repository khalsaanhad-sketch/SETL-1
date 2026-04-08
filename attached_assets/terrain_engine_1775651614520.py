import numpy as np
from scipy.ndimage import generic_filter

# ---- ADD THESE ----

def compute_slope_grid(elevation_grid, cell_size=30):
    dzdx = np.gradient(elevation_grid, axis=1) / cell_size
    dzdy = np.gradient(elevation_grid, axis=0) / cell_size

    slope = np.sqrt(dzdx**2 + dzdy**2)
    slope_deg = np.degrees(np.arctan(slope))

    return slope_deg


def compute_roughness_grid(elevation_grid):
    def std_func(x):
        return np.std(x)

    return generic_filter(elevation_grid, std_func, size=3)


# ---- IMPORTANT ----
# Your existing get_terrain() must return:
#
# return {
#     "elevation_grid": elevation_grid
# }
#
# If not, adapt your API to fetch a small DEM grid.