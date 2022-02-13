/*
 * Developer    : WM (Witold@air-stream.com.au)
 * Company      : Airstream Components
 * Date         : 01/10/2018
 *
 * All code (c)2018 Airstream Components. All rights reserved
 *
 * Description	: iZone power monitoring interface.
 *
 * Info			: Send all commands to /PowerCommand,
 *              : Send all status/config request to /PowerRequest
 *
 * Revisions:
 * 1.0 - initial release
 * 1.1 - Server query
 * 1.2 - added change power factor command
 *     - added change channel name command
 *     - added pair a power monitor command
 * 1.3 - added "PowerCostOfPower" setting command
 *       added "PowerEmissions" setting command
 *       "PowerPair" command is no longer required, just use the pair command from lighing system
 * 1.4 - added "ChannelAddToTotal" command and setting in status
  */
 
 typedef enum BATTERYLEVEL
{
  CpmBatt_Critical = 0, /* reading <600                                        */
  CpmBatt_Low      = 1, /* 600-700                                             */
  CpmBatt_Normal   = 2, /* 700-800                                             */
  CpmBatt_Full     = 3  /* >800                                                */
}CpmBatt_e;
 
 
//**********************************************************
// request information
//**********************************************************
{
	"PowerRequest":
	{
		"Type": x,
		"No": y,
		"No1": z
	}
}
//where 
//x - data type to be requested. 
//	  1 - power monitor configuration
//    2 - power monitor current status
//y - currently no use
//z - currently no use

//**********************************************************
// add to total for solar diverter calculations setting
//**********************************************************
{
	"ChannelAddToTotal":
	{
		"Device":a,
		"Channel":b,
		"AddToTotal":c
	}
}
// where:
// a - is the device number (0-4)
// b - is the channel number (0-2)
// c - is the setting flag (0-1)

// change the power emmissions setting
{
	"PowerEmissions": x
}
// where:
// x - is the new setting in gCO2e (GHG)

// change the cost of power setting 
{
	"PowerCostOfPower": x
}
// where:
// x - is the new setting in 0.01 cents
 
 NOTE: this command is no longer required
//**********************************************************
// Pair a power monitor device
//**********************************************************
{
	"PowerPair":
	{
		"Pair":1,
		"DevNo":a
	}
}
// where:
// a - is the device number (0-4)


//**********************************************************
// Change channel name command
//**********************************************************
{
	"ChannelName":
	{
		"Device":a,
		"Channel":b,
		"String":c
	}
}
// where:
// a - is the device number (0-4)
// b - is the channel number (0-2)
// c - is the new channel name

//**********************************************************
// change power factor command
//**********************************************************
{
	"PowerFactor":int
}
// where int is the power factor *100 => 1 to 100


//**********************************************************
// change tag line 1
//**********************************************************
{
	"Tag1":"string"
}
// where string is the tag (upto 32 chars including \0)

//**********************************************************
// change tag line 2
//**********************************************************
{
	"Tag2":"string"
}
// where string is the tag (upto 32 chars including \0)

//**********************************************************
// change system voltage
//**********************************************************
{
	"SystemVoltage":int
}
// where int is the voltage of the power system monitored

//**********************************************************
// Enable/disable device
//**********************************************************
{
	"DeviceEnable":
	{
		"Device":a,
		"Enable":b
	}
}
// where:
// a - is the device number (0-4)
// b - is the enable flag (0-1)

//**********************************************************
// Enable/disable channel on a device
//**********************************************************
{
	"ChannelEnable":
	{
		"Device":a,
		"Channel":b,
		"Enable":c
	}
}
// where:
// a - is the device number (0-4)
// b - is the channel number (0-2)
// c - is the enable flag (0-1)

//**********************************************************
// Enable/disable channel on a device
//**********************************************************
{
	"ChannelGroup":
	{
		"Device":a,
		"Channel":b,
		"Group":c
	}
}
// where:
// a - is the device number (0-4)
// b - is the channel number (0-2)
// c - is the enable flag (0-1)

//**********************************************************
// change generate setting of a channel
//**********************************************************
{
	"ChannelGenerate":
	{
		"Device":a,
		"Channel":b,
		"Generate":c
	}
}
// where:
// a - is the device number (0-4)
// b - is the channel number (0-2)
// c - is the generate setting (0-1); 0 - power consumption, 1 - power generation

//**********************************************************
// power monitor status information
//**********************************************************
{
	"AirStreamDeviceUId":"123456794",
	"DeviceType":"ASH",
	"PowerMonitorStatus":
	{
		"leasReadingNo":int,				// last reading number (internal)
		"Dev":[
			{
				"Ok":int,					// device 1 OK flag
				"Batt":CpmBatt_e,			// device 1 battery level
				"Ch":[
					{
						"Pwr":int			// device 1 channel 1 (1) power [W] 
					},
					{
						"Pwr":int 			// device 1 channel 2 (2) power [W] 
					},
					{
						"Pwr":int 			// device 1 channel 3 (3) power [W] 
					}
				]
			},
			{
				"Ok":int,					// device 2 OK flag
				"Batt":CpmBatt_e,			// device 2 battery level
				"Ch":[
					{
						"Pwr":int			// device 2 channel 1 (4) power [W] 
					},
					{
						"Pwr":int 			// device 2 channel 2 (5) power [W] 
					},
					{
						"Pwr":int 			// device 2 channel 3 (6) power [W] 
					}
				]
			},
			{
				"Ok":int,					// device 3 OK flag
				"Batt":CpmBatt_e,			// device 3 battery level
				"Ch":[
					{
						"Pwr":int			// device 3 channel 1 (7) power [W] 
					},
					{
						"Pwr":int 			// device 3 channel 2 (8) power [W] 
					},
					{
						"Pwr":int 			// device 3 channel 3 (9) power [W] 
					}
				]
			},
			{
				"Ok":int,					// device 4 OK flag
				"Batt":CpmBatt_e,			// device 4 battery level
				"Ch":[
					{
					{
						"Pwr":int			// device 4 channel 1 (10) power [W] 
					},
					{
						"Pwr":int 			// device 4 channel 2 (11) power [W] 
					},
					{
						"Pwr":int 			// device 4 channel 3 (12) power [W] 
					}
				]
			},
			{
				"Ok":int,					// device 5 OK flag
				"Batt":CpmBatt_e,			// device 5 battery level
				"Ch":[
					{
						"Pwr":int			// device 5 channel 1 (13) power [W] 
					},
					{
						"Pwr":int 			// device 5 channel 2 (14) power [W] 
					},
					{
						"Pwr":int 			// device 5 channel 3 (15) power [W] 
					}
				]
			}
		]
	}
}

//**********************************************************
// power monitor configuration information
// Info: there are 5 devices, each device has 3 channel for
//       monitoring circuits. Each device can be enabled and
//       disabled. Each channel in the device can be anbled
//       and disabled.
//**********************************************************
{
	"AirStreamDeviceUId":"000005273",
	"DeviceType":"ASH",
	"PowerMonitorConfig":
	{
		"Enabled":int,						// power monitor system enabled flag
		"Tag1":"string",					// power monitor tag line 1
		"Tag2":"string",					// power monitor tag line 2
		"Voltage":int,						// power system voltage
		"PF":int,							// power factor
		"CostOfPower":int,					// cost of power in 0.01 cents
		"Emissions":int,					// emissions in gCOe per kWh
		"Devices":[
			{
				"Enabled":int,				// device 1 enabled flag
				"Channels":[
					{						// device 1 channel 1 (1):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 1 channel 2 (2):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 1 channel 3 (3):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					}
				],
			},
			{
				"Enabled":int,				// device 2 enabled flag
				"Channels":[
					{						// device 2 channel 1 (4):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 2 channel 2 (5):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 2 channel 3 (6):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					}
				]
			},
			{
				"Enabled":int,				// device 3 enabled flag
				"Channels":[
					{						// device 3 channel 1 (7):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 3 channel 2 (8):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 3 channel 3 (9):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					}
				]
			},
			{
				"Enabled":int,				// device 4 enabled flag
				"Channels":[
					{						// device 4 channel 1 (10):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 4 channel 2 (11):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 4 channel 3 (12):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					}
				]
			},
			{
				"Enabled":int,				// device 5 enabled flag
				"Channels":[
					{						// device 5 channel 1 (13):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 5 channel 2 (14):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					},
					{						// device 5 channel 3 (15):
						"Name":"string",  	// name
						"GrNo":int,			// group number
						"Consum":int,		// consumption
						"Enabled":int,		// channel enabled
						"AddToTotal":int	// add to total
					}
				]
			}
		]
	}
}





