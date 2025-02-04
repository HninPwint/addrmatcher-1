from region import Region
from hierarchy import GeoHierarchy
from operator import lt, le, ge, gt
from rapidfuzz import fuzz
import pandas as pd
import re
import glob
import numpy as np
from pyarrow import fs
import pyarrow.parquet as pq
from sklearn.neighbors import BallTree
from ast import literal_eval


class GeoMatcher:

    __slot__ = ("_hierarchy", "_filename", "_data")

    def __init__(self, hierarchy, filename=""):
        self._hierarchy = hierarchy

        # if no filename provided, look for the dataset in the default folder: data/[country]
        if isinstance(filename, str):
            if filename.strip() == "":
                self._filename = glob.glob(
                    "data\\\\" + self._hierarchy.name + "\*.{}".format("csv")
                )
            else:
                self._filename = filename
        else:
            self._filename = filename

        # load the dataset into the data frame
        # the matcher only allows CSV or parquet file
        if isinstance(self._filename, str):
            if self._filename.lower().endswith(".parquet"):
                self._data = pd.read_parquet(self._filename)
            elif self._filename.lower().endswith(".csv"):
                self._data = pd.read_csv(self._filename, dtype="str")
            else:
                raise ValueError("Filename should be a CSV or a Parquet file")
        else:
            self._data = []
            for file in self._filename:
                if file.lower().endswith(".parquet"):
                    self._data.append(pd.read_parquet(file))
                elif file.lower().endswith(".csv"):
                    self._data.append(pd.read_csv(file, dtype="str"))
                else:
                    raise ValueError("Filename should be a CSV or a Parquet file")

    def get_region_by_address(
        self,
        address,
        similarity_threshold=0.9,
        top_result=True,
        regions=[],
        operator=None,
        region="",
    ):
        """
        perform address based matching and return the corresponding region
        e.g. administrative level or statistical are

        :param string address:
        """

        # initiate the result
        addresses = pd.DataFrame()

        # if datasets are stored in multiple file
        if isinstance(self._data, list):

            # calculate the distance (Levenshtein Distance) between the input address
            # and the entire reference addresses dataset
            # [all special characters are removed]
            for data in self._data:
                data["RATIO"] = data["FULL_ADDRESS"].apply(
                    lambda x: fuzz.ratio(
                        re.sub(r"[\W_]+", "", address.lower()),
                        re.sub(r"[\W_]+", "", x.lower()),
                    )
                )

                # if similarity score is larger then the threshold,
                # there is a possibility the addresses are similar
                # will need to select the highest score later on
                if addresses.shape[0] == 0:
                    addresses = data[data["RATIO"] >= similarity_threshold * 100.0]
                else:
                    addresses = addresses.append(
                        data[data["RATIO"] >= similarity_threshold * 100.0],
                        ignore_index=True,
                    )
        # if datasets are stored in a single file
        else:
            # calculate the distance (Levenshtein Distance) between the input address
            # and the entire reference addresses dataset
            # [all special characters are removed]
            self._data["RATIO"] = self._data["FULL_ADDRESS"].apply(
                lambda x: fuzz.ratio(
                    re.sub(r"[\W_]+", "", address.lower()),
                    re.sub(r"[\W_]+", "", x.lower()),
                )
            )

            # if similarity score is larger then the threshold,
            # there is a possibility the addresses are similar
            # will need to select the highest score later on
            addresses = self._data[self._data["RATIO"] >= similarity_threshold * 100.0]

        # get the regions that users selected
        selected_regions = self._hierarchy.get_regions_by_name(
            operator=operator, name=region, names=regions
        )

        # get the columns only
        selected_columns = []
        for reg in selected_regions:
            if reg not in selected_columns:
                selected_columns.append(reg.col_name)

        # deleted later
        selected_columns.append("FULL_ADDRESS")
        selected_columns.append("RATIO")

        # remove empty element, if exists
        selected_columns = list(filter(None, selected_columns))

        # if there are possible similar address found
        if addresses.shape[0] > 0:
            # return the most similar address only
            if top_result:

                # sort the addresses based on the similarity score
                addresses = addresses.sort_values(
                    by="RATIO", ascending=False
                ).reset_index(drop=True)

                return addresses.head(1)[selected_columns]

            # return all the similar addresses
            else:

                return addresses[selected_columns].sort_values(
                    by="RATIO", ascending=False
                )
        else:
            return None

        # def cartesian(self, latitude, longitude, elevation=0):

        #     # Convert to radians
        #     latitude = latitude * (math.pi / 180)
        #     longitude = longitude * (math.pi / 180)

        #     R = 6371  # 6378137.0 + elevation  # relative to centre of the earth
        #     X = R * math.cos(latitude) * math.cos(longitude)
        #     Y = R * math.cos(latitude) * math.sin(longitude)
        #     Z = R * math.sin(latitude)
        #     return (X, Y, Z)

        # def get_region_by_coordinates(self, lat, lon, regions=[], operator=None, region=""):

        places = []
        addresses = pd.DataFrame()
        address = pd.DataFrame()
        # Calculate the catesian coordinates of the input
        cartesian_coord = self.cartesian(lat, lon)

        index = []
        if isinstance(self._data, list):
            for data in self._data:
                data["CARTESIAN_COOR"] = data["CARTESIAN_COOR"].apply(
                    lambda x: literal_eval(str(x))
                )
                places = data["CARTESIAN_COOR"].tolist()
                # Build the tree
                tree = spatial.KDTree(places)
                # Find the nearest point to the input
                closest = tree.query([cartesian_coord], p=2)

                addresses = addresses.append(data.iloc[closest[1][0], :])

        else:
            self._data["CARTESIAN_COOR"] = self._data["CARTESIAN_COOR"].apply(
                lambda x: literal_eval(str(x))
            )
            places = self._data["CARTESIAN_COOR"].tolist()

            # Build the tree
            tree = spatial.KDTree(places)

            # Find the nearest point to the input
            closest = tree.query([cartesian_coord], p=2)

            # Get the index of the first nearest neighbour / point / row
            addresses = addresses.append(self._data.iloc[closest[1][0], :])

        if addresses.shape[0] > 1:
            places = addresses["CARTESIAN_COOR"].tolist()
            # Build the tree
            tree = spatial.KDTree(places)
            # Find the nearest point to the input
            closest = tree.query([cartesian_coord], p=2)

            address = addresses.iloc[closest[1][0], :]

        else:
            address = address.append(addresses.iloc[0])

        selected_columns = self._hierarchy.get_regions_by_name(
            operator=operator, name=region, names=regions, attribute="col_name"
        )

        # deleted later
        selected_columns.append("FULL_ADDRESS")

        # remove empty element, if exists
        selected_columns = list(filter(None, selected_columns))

        return address[selected_columns]

    def _load_parquet(self, lat, lon, distance):

        local = fs.LocalFileSystem()
        df = pq.read_table(
            self._filename,
            filesystem=local,
            filters=[
                ("LATITUDE", ">=", lat - distance),
                ("LATITUDE", "<=", lat + distance),
                ("LONGITUDE", ">=", lon - distance),
                ("LONGITUDE", "<=", lon + distance),
            ],
        ).to_pandas()

        return df

    def _ensure_lat_lon_within_range(self, lat, lon):

        # MAX and MIN coordinates of AU addresses
        lat_min = -43.58301104
        lat_max = -9.23000371
        lon_min = 96.82159219
        lon_max = 167.99384663

        # Ensure Latitudge within the AU range
        lat = max(lat, lat_min)
        lat = min(lat, lat_max)

        # Ensure longitutde within the AU range
        lon = max(lon, lon_min)
        lon = min(lon, lon_max)

        return lat, lon

    def _filter_for_rows_within_mid_distance(df, lat, lon, mid_distance):

        mid_df = df[
            df.LATITUDE.between(lat - mid_distance, lat + mid_distance)
            & df.LONGITUDE.between(lon - mid_distance, lon + mid_distance)
        ]

        return mid_df

    def get_region_by_coordinates(
        self, lat, lon, n=1, km=1, regions=[], operator=None, region=""
    ):

        min_distance = 0
        # 1 lat equals 110.574km
        distance = (km if km else 1) / 110.574

        ## 1. Initial distance setting according to lat/lon arguments to ensure lat/lon within AU range
        lat, lon = self._ensure_lat_lon_within_range(lat, lon)

        ## 2. Make the first load of GNAF dataset
        gnaf_df = self._load_parquet(lat, lon, distance)

        # 2.a If the desired count of addresses not exist, increase the radius
        while gnaf_df.shape[0] < n:
            min_distance = distance
            distance *= 2

            gnaf_df = self._load_parquet(lat, lon, distance)

        # 2.b Keep reducing the size of rows if more than 10k adddresses are found within the radius
        # Take the median distance to reduce
        # This is to limit the number of datapoint to build the Ball tree in the next step
        while gnaf_df.shape[0] >= n + 10000:
            middle_distance = (distance - min_distance) / 2

            gnaf_df = gnaf_df[
                gnaf_df.LATITUDE.between(lat - middle_distance, lat + middle_distance)
                & gnaf_df.LONGITUDE.between(
                    lon - middle_distance, lon + middle_distance
                )
            ]

            distance = middle_distance

        ## 3. Build the Ball Tree and Query for the nearest within k distance
        ball_tree = BallTree(
            np.deg2rad(gnaf_df[["LATITUDE", "LONGITUDE"]].values), metric="haversine"
        )
        distances, indices = ball_tree.query(
            np.deg2rad(np.c_[lat, lon]), k=min(n, gnaf_df.shape[0])
        )
        # Get indices of the search result, Extract pid and calculate distance(km)
        indices = indices[0].tolist()
        pids = gnaf_df.ADDRESS_DETAIL_PID.iloc[indices].tolist()
        distance_map = dict(zip(pids, [distance * 6371 for distance in distances[0]]))

        ## 4. Filter the GNAF dataset by address_detail_pid
        bool_list = gnaf_df["ADDRESS_DETAIL_PID"].isin(pids)
        final_gnaf_df = gnaf_df[bool_list]

        final_gnaf_df["DISTANCE"] = final_gnaf_df["ADDRESS_DETAIL_PID"].map(
            distance_map
        )

        return final_gnaf_df.sort_values("DISTANCE")
