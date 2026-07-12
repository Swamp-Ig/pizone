"""Shared iPower mock responses for tests."""

POWER_CONFIG = {
    "Enabled": 1,
    "Tag1": "Grid",
    "Tag2": "Monitor",
    "Voltage": 240,
    "PF": 100,
    "CostOfPower": 2520,
    "Emissions": 870,
    "Devices": [
        {
            "Enabled": 1,
            "Name": "Grid",
            "Channels": [
                {
                    "Enabled": 1,
                    "Name": "Grid",
                    "GrNo": 1,
                    "Generate": 0,
                    "AddToTotal": 1,
                },
                {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
            ],
        },
        *[
            {
                "Enabled": 0,
                "Name": "",
                "Channels": [
                    {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                    {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                    {"Enabled": 0, "Name": "", "GrNo": 255, "Generate": 0, "AddToTotal": 0},
                ],
            }
            for _ in range(4)
        ],
    ],
}

POWER_STATUS = {
    "LastReadingNo": 394,
    "Dev": [
        {
            "Ok": 0,
            "Batt": 3,
            "Ch": [
                {"Pwr": 1500},
                {"Pwr": 0},
                {"Pwr": 0},
            ],
        },
        *[
            {
                "Ok": 1,
                "Batt": 3,
                "Ch": [
                    {"Pwr": 0},
                    {"Pwr": 0},
                    {"Pwr": 0},
                ],
            }
            for _ in range(4)
        ],
    ],
}
