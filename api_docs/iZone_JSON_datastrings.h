/*
 * Developer    : WM (Witold@air-stream.com.au)
 * Company      : Airstream Components
 * Date         : 21/08/2017
 *
 * All code (c)2016 Airstream Components. All rights reserved
 *
 * Description	: This JSON document describes the new interface 
 * 				  for iZone. This interface will include information
 *                and command for configuration all parameters allowing
 *                the implementation of the naked iZone (app only control)
 *
 * Revisions:
 * 1.0 - initial release
 * 1.1 - added ScroogeMode command
 *     - added ReverseDampers command
 *     - added Pass, Scrooge, ReverseDampers, UnitOpt.RA/Master/Zones/History/Slave to system status
 * 1.2 - added DamperSkip, Zone BalanceMax and BalanceMin to zone status 
 *     - added DamperSkip command
 *     - added Zone BalanceMax command
 *     - added Zone BalanceMin command
 * 1.3 - added ChangePass command
 *     - added ChangeRfCh command
 * 1.4 - updated request information list
 * 1.5 - added HideInduct command
 * 1.6 - added system lock command
 *	   - added wired LEDs command
 * 1.7 - added "RfPair" command (for temperature sensors)
 * 1.8 - added SysSleepTimer command
 * 1.9 - added constant control by area commands
 * 1.10- added "StaticP" command to change static pressure setting for midea type of units
 *       added "OpenDampersWhenOff" command to open dampers when AC system is turned off
 *       added "ShowActTemps" command to show actual temperature in the modern zone list
 *       added "TemperzoneQuietMode" command to enable to outdoor fan quiet mode for temperzone AC units.=
 *-----------------------------------------------------------------------------
 * 
 */
 
 enum
 {
	SysOn_Off,
	SysOn_On
 }SysOn_e;
 
 enum
 {
	SysMode_Cool = 1,
	SysMode_Heat,
	SysMode_Vent,
	SysMode_Dry,
	SysMode_Auto
 }SysMode_e;
 
 enum
 {
	SysFan_Low = 1,
	SysFan_Med,
	SysFan_High,
	SysFan_Auto,
	SysFan_Top,
	SysFan_NonGasHeat = 99
 }SysFan_e;
 
 enum
 {
	ReturnAirSensor_Ras = 1,
	ReturnAirSensor_Master,
	ReturnAirSensor_Zones
 }ReturnAirSensor_e;
 
enum
{
  UnitBrand_Panasonic_Toshiba = 1,
  UnitBrand_Daikin,
  UnitBrand_Mitsubishi_Electric,
  UnitBrand_LG301,
  UnitBrand_LG310,
  UnitBrand_Fujitsu,
  UnitBrand_Samsung,
  UnitBrand_Temperzone,
  UnitBrand_Mitsubishi_Heavy_Industries,
  UnitBrand_Gas_Heat_Add_On_Cool,
  UnitBrand_Generic,
  UnitBrand_UnitBrand_Unknown,
  UnitBrand_Hitachi,
  UnitBrand_AA_GenIii,
  UnitBrand_Fujitsu_Intesis,
  UnitBrand_Lg485,
  UnitBrand_YorkAc,
  UnitBrand_HaierAc
}UnitBrand_e;

enum
{
  GasHeatType_HeatOnly_1SpeedFan,
  GasHeatType_CoolOnly_1SpeedFan,
  GasHeatType_1HeatAnd1Cool_1SpeedFan,
  GasHeatType_2HeatAnd1Cool_1SpeedFan,
  GasHeatType_1HeatPump_1SpeedFan,
  GasHeatType_1HeatPump_3SpeedFan,
  GasHeatType_1HeatPump_1Heat_1SpeedFan,
  GasHeatType_2HeatPump_1Heat_1FanSpeed,
  GasHeatType_1GasHeat,
  GasHeatType_2GasHeat_2Cool_1FanSpeed,
  GasHeatType_Remote_On_Off,
  GasHeatType_AA_GEN_III
}GasHeatType_e;
 
 enum
{
  FanAutoType2Speed,
  FanAutoType3Speed,
  FanAutoTypeVariableSpeed,
  FanAutoType4Speed
}FanAutoType_e;
 
enum
{
  TemperzoneModeTypeNoExpansion,
  TemperzoneModeTypeSingleExpansion,
  TemperzoneModeTypeSeriesExpansion,
  TemperzoneModeTypeDryMode
}TemperzoneModeType_t;

enum
{
  TemperzoneFanType3Speed = 1,
  TemperzoneFanTypeVariableSpeed = 0
}TemperzoneFanType_t;
 
enum
{
  OemMake_Airstream,
  OemMake_Metalflex,
  OemMake_Westaflex
}OemMake_e;
 
 
 //************************** SYSTEM JSON ***********************************//
 
{
	"AirStreamDeviceUId": "001EC0028041", 
	"DeviceType": "ASH",
	"SystemV2":	{
		"SysOn": int,					// AC system on: SysOn_e
		"SysMode": int,					// AC system mode: SysMode_e
		"SysFan": int,					// AC system fan: SysFan_e
		"SleepTimer": int,				// number of minutes to set the timer for, 0 = timer off
		"Supply": int,					// supply air temperature (x100)
		"Setpoint": int, 				// unit setpoint temperature (x100)
		"Temp": int,					// return air temperature (x100)
		"RAS": int,						// return air sensor mode: ReturnAirSensor_e
		"CtrlZone": int,				// zone number to control the unit 0-13, if 15, the use unit setpoint.
		"Tag1": string,					// first line text
		"Tag2": string,					// second line text
		"Warnings": "none",				// indicates current warnings/errors: "none", "filter"
		"ACError": string,				// 3 character error code. (" OK" = no error)
		"EcoLock": int,					// if true, setpoints (zones and system) can only be set within EcoMax and EcoMin
		"EcoMax": int,					// maximum setpoint temperature if EcoLock = true (x100)
		"EcoMin": int,					// minimum setpoint temperature if EcoLock = true (x100)
		"NoOfConst": int,				// number of constants in the system
		"NoOfZones": int,				// number of zones in the system
		"SysType": int,					// system type, 310 requires supprot for unit control related functions, 210 no unit control
		//"AirflowLock": int,			    // if true airflow adjustment not possible
		//"UnitType": int,				// UnitBrand_e
		//"UnitLocked": int,				// true/false, if true only display "Your system is locked. Enter keycode on iZone controller to unlock the system"
		//"FreeAir": string,				// disabled - do not show the button at all, on - FreeAir function is on, off - FreeAir function is off
		//"FanAuto": string,				// “disabled” – fan speeds allowed on the AC unit control screen – Low, Medium, High
										// “2-speed” – fan speeds allowed on the AC unit control screen – Low, High, Auto
										// “3-speed” – fan speeds allowed on the AC unit control screen – Low, Medium, High, Auto
										// “var-speed” – fan speeds allowed on the AC unit control screen – Low, Medium, High, Auto
		
		// new
		"iSaveEnable": int,
		"iSaveOn": int,
		*"LockCode": string,				// up to 6 digits
		"LockStatus": int,				// locked flag, 1 - unit locked, only display "Your system is locked. Enter keycode on iZone controller to unlock the system"
		"LockOn": int,					// lock enabled
		"FanAutoEn": int,				// fan auto mode enabled
		"FanAutoType": int,				// fan auto mode fan type: FanAutoType_e
		"FanCapacity": int,				// fan auto mode fan capacity (airflow)
		"FanUnitCapacity": int,			// fan auto mode unit capacity
		"FilterWarn": int,				// filter wanrning setting (months)
		"iZoneOnOff": int,				// iZone controls AC unit on/off function, 0 - disabled, 1 - enabled
		"iZoneMode": int,				// iZone controls AC unit mode function,  0 - disabled, 1 - enabled
		"iZoneFan": int,				// iZone controls AC unit fan function, 0 - disabled, 1 - enabled
		"iZoneSetpoint": int,			// iZone controls AC unit setpoint function, 0 - disabled, 1 - enabled
		"ExtOnOff": int,				// AC unit controls iZone on/off function, 0 - disabled, 1 - enabled
		"ExtMode": int,					// AC unit controls iZone mode function, 0 - disabled, 1 - enabled
		"ExtFan": int,					// AC unit controls iZone fan function, 0 - disabled, 1 - enabled
		"ExtSetpoint": int,				// AC unit controls iZone setpoint function, 0 - disabled, 1 - enabled
		"DamperTime": int,				// damper time setting (senconds), 0 - automatic
		"AutoOff": int,					// auto off enable, 0 - disabled, 1 - enabled
		"RoomTempDisp": int,			// display room temperature,  0 - disabled, 1 - enabled
		"RfCh": int,					// RF channel number (1-8)
		"AutoModeDeadB": int,			// auto mode deadband (x100)
		"WiredLeds": int,				// wired sensor leds setting 
		"AirflowLock": int,
		"AirflowMinLock": int,
		"OutOfViewRAS": int,
		
		"AcUnitBrand": int,				// type of the connected AC unit: UnitBrand_e

		"OemMake": int,					// system make: OemMake_e
		"HideInduct": int, 				// hide induct temperature setting
		
		"ReverseDampers":1,				// reverse dampers, 0 - disabled, 1 - enabled
		"Scrooge":0,					// scrooge mode, 0 - disabled, 1 - enabled
		"Pass":string,					// system configuration password
		
		"CnstCtrlAreaEn": int,			// enable the function, 0 - disabled, 1 - enabled
		"CnstCtrlArea": int,			// zone area setting for the constant control by area setting
		
		"StaticP": int,					// static pressure setting for Midea type of units 0-lowest -> 4-highest
		"OpenDampersWhenOff": int,		// open dampers when AC system is off setting
		"ShowActTemps": int,			// in the modern zone list show actual temperatures instead of airflow
		
		"UnitOpt": {
			"RA":0,						// display RA sensor option, 0 - disabled, 1 - enabled
			"Master":1,					// display Master sensor option, 0 - disabled, 1 - enabled
			"Zones":1,					// display Zones sensor option, 0 - disabled, 1 - enabled
			"History":0,				// display unit history option, 0 - disabled, 1 - enabled
			"SlaveOpt":0				// display Master/Slave options, 0 - disabled, 1 - enabled
		}

		"Temperzone": {
			"HeatSetpoint": int,		// temperzone AC unit heat mode setpoint
			"CoolSetpoint": int,		// temperzone AC unit cool mode setpoint
			"FanType": int,
			"ModeType": int,
			"Quiet": int,				// temperzone outdoor fan quiet mode
		}
		
		"GasHeat": {
			"Type": int, 				// universal unit type control: GasHeatType_e
			"MinRunTime": int,			// minimum run time setting
			"AnticycleTime": int,		// anticycle time setting
			"StageOffset": int,			// stage offset setting
			"StageDelay": int,			// stage delay time setting
			"CycleFanCool": int,		// cycle fan in cool mode
			"CycleFanHeat": int,		// cycle fan in heat mode
		}
	}
}

// iZone V2 system commands
// configuration

// set temperzone AC unit outdoor fan quiet mode
{
	"TemperzoneQuietMode": x
}
// where:
// x - is the settting: 0 - not quiet mode, 1 - quiet mode

// show actual temperatures in the modern zones list instead of airflow
{
	"ShowActTemps": x
}
// where:
// x - is the settting: 0 - show airflow, 1 - show actual temperatures

// open dampers when the AC unit is turned off
{
	"OpenDampersWhenOff": x
}
// where:
// x - is the settting: 0 - close dampers, 1 - open dampers

// set static pressure setting for midea type of units
{
	"StaticP": x
}
// where:
// x - is the settting 0-lowest, 4-highest

// enable constant control by area setting
{
	"CnstCtrlAreaEn":x
}
// where:
// x - is the enable flag

// set zone area for the constant control by area function
{
	"CnstCtrlArea":x
}
// where:
// x - is the area to be covered by the constant

// system sleep timer
{
	"SysSleepTimer":x
}
// where:
// x - is time in minutes

// pair wireless sensor command
{
	"RfPair":1
}

// hide induct temperature command
{
	"HideInduct":a
}
// where:
// a -  0 - not hidden, 1 - hidden

// change RF channel command
{
	"ChangeRfCh": a
}
// where:
// a - is the new channge (1 - 8)

// change configuration password
{
	"ChangePass": a
}
// where:
// a - is the new password (16 chars, including \0)

// reverse dampers setting
{
	"ReverseDampers":a
}
// where:
// a -  0 - disabled, 1 - enabled

// scrooge mode setting
{
	"ScroogeMode":a
}
// where:
// a -  0 - disabled, 1 - enabled

// return air sensor setting
{
	"RASSet":a
}
// where:
// a - return air sensor: ReturnAirSensor_e

// master zone (for master return air sensor setting)
{
	"MasterZone":a
}
// where:
// a - is the zone number to control the unit (0 = zone 1,...)

// set tag line 1
{
	"SysTag1":a
}
// where:
// a - is the new tag line, up to 32 characters including string termination

// set tag line 2
{
	"SysTag2":a
}
// where:
// a - is the new tag line, up to 32 characters including string termination

// economy lock
{
	"EconomyLock":a
}
// where:
// a - is the economy lock setting, 0 = unlocked, 1 = locked

// economy maxinum temperature setting
{
	"EconomyMax":a
}
// where:
// a - is the maximum setpoint temperature allowed x100, value limits: 1500 <= a <= 3000, steps of 50

// economy minimum temperature setting
{
	"EconomyMin":a
}
// where:
// a - is the minimum setpoint temperature allowed x100, value limits: 1500 <= a <= 3000, steps of 50

// set number of zones
{
	"NoOfZones":a
}
// where:
// a - is the new number of zones, value limits a <= 14

// set number of constants
{
	"NoOfConstants":a
}
// where:
// a - is the new number of constants, value limits a <= NoOfZones

// enable iSave option
{
	"EnableiSave":a
}
// where:
// a - is iSave enable flag, 0 = no iSave option, 1 = iSave anabled

// lock system (timer)

to be implemented
{
	"LockSystem":
	{
		"Lock": int,					// 1 - lock, 0 - unlock
		"LockCode": string,				// 6 digit
		"LockDays": int					// 30?
		// default code: 2705
	}
}

// enable fan auto option
{
	"FanAutoEn":a
}
// where:
// a - is fan auto function enable flag, 0 = function disabled, 1 = function enabled.

// set fan auto type
{
	"FanAutoType":a
}
// where:
// a - is fan type for fan auto function: FanAutoType_e

// set fan airflow for fan auto function
{
	"FanCapacity":a
}
// where:
// a - is the new fan airflow rating

// set unit capacity for fan auto function
{
	"FanUnitCapacity":a
}
// where:
// a - is the new AC unit capacity in kW

// set filter warning period
{
	"FilterWarn":a
}
// where:
// a - is the new filter warning period in months: valid values: 0 (disabled), 3, 6, 12

// enable control of On/Off function from unit controller
{
	"iZoneOnOff":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of mode function from unit controller
{
	"iZoneMode":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of fan function from unit controller
{
	"iZoneFan":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of setpoint function from unit controller
{
	"iZoneSetpoint":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of On/Off function from iZone controller
{
	"ExtOnOff":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of mode function from iZone controller
{
	"ExtMode":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of fan function from iZone controller
{
	"ExtFan":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// enable control of setpoint function from iZone controller
{
	"ExtSetpoint":a
}
// where:
// a - is the new setting, 0 = no contro1, 1 - control enabled

// set damper control time
{
	"DamperTime":a
}
// where:
// a - is the new damper time in seconds, 0 = automatic damper timing 

// enable auto off function
{
	"AutoOff":a
}
// where:
// a - is the new enabled flag, 0 = function disabled, 1 = function enabled

// enable room temperature display
{
	"RoomTempDisp":a
}
// where:
// a - is the new enabled flag, 0 = function disabled, 1 = function enabled

// set auto mode dead band
{
	"AutoModeDeadB":a
}
// where:
// a - is the new dead band setting x100 degC, value limits 75 <= a <= 500

// set wired leds function
{
	"SetWiredLeds": int				// 1 - enabled, 0 - disabled
}


// enable airflow lock (both min and max)
{
	"AirflowLock":a
}
// where:
// a - is the new setting flag, 0 = lock disabled (unlocked), 1 = locked


// enable min airflow lock (min only, max can still be changed
{
	"AirflowMinLock":a
}
// where:
// a - is the new setting flag, 0 = lock disabled (unloecked), 1 = locked

// set temperzone unit control setpoints
{
	"TemperzoneSettingsSetpoints":
	{
		"HeatSetpoint":a,
		"CoolSetpoint":b
	}
}
// where:
// a - is the new setpoint in heat mode x100; value limits 3000 <= a <= 4000
// b - is the new setpoint in cool mode x100; value limits 500 <= b <= 1500

// set temperzone unit options
{
	"TemperzoneSettingsUnit":
	{
		"FanType":a,
		"ModeType":b
	}
}
// where:
// a - is the new fan type:	TemperzoneFanType_t
// b - is the new mode type: TemperzoneModeType_t

// change universal controller settings
{
	"GasHeatSettings":{
		"Type":a,
		"MinRunTime":b,
		"AnticycleTime":c,
		"StageOffset":d,
		"StageDelay":e,
		"CycleFanCool":f,
		"CycleFanHeat":g
	}
}
// where:
// a - is the universal unit type: GasHeatType_e
// b - is the minimum run time in minutes: value limits: 2 <= b <= 10
// c - is the anticycle time in minutes: value limits: 2 <= c <= 10
// d - is the stage offset x10 in degC: value limits: 20 <= d <= 50
// e - is the stage delay time in mimutes: value limits: 5 <= e <= 15
// f - is the cycle fan in cool mode flag: 0 = run fan continously in cool, 1 - cycle the fan together with the ac unit in cool
// g - is the cycle fan in heat mode flag: 0 = run fan continously in heat, 1 - cycle the fan together with the ac unit in heat


// operation

// change unit on/off
{
	"SysOn":a
}
// where:
// a - is the on/off setting, 0 = stop air con, 1 = run air con

// change unit mode 
{
	"SysMode":a
}
// where:
// a - is the new AC unit mode: SysMode_e


// change unit fan
{
	"SysFan":a
}
// where:
// a - is the new fan speed: SysFan_e

// change unit setpoint
{
	"SysSetpoint":a
}
// where:
// a - is the new setpoint x100, value limits: 1500 <= a <= 3000

// change iSave on/off
{
	"iSaveOn":a
}
// where:
// a - is the iSave On/Off setting: 0 = iSave is off, 1 = iSave is on




// send all commands to /iZoneCommandV2

//************************** ZONES JSON ************************************//
enum
{
	ZoneType_OpenClose = 1,
	ZoneType_Constant,
	ZoneType_Auto
}ZoneType_e;

enum
{
	ZoneMode_Open = 1,
	ZoneMode_Close,
	ZoneMode_Auto,
	ZoneMode_Override,
	ZoneMode_Constant
}ZoneMode_e;

enum
{
	RfSignalLevel_Full = 0,
	RfSignalLevel_Half,
	RfSignalLevel_Quarter,
	RfSignalLevel_None
}RfSignalLevel_e;

enum
{
  BatteryLevel_Full,
  BatteryLevel_Half,
  BatteryLevel_Empty
}BatteryLevel_e;

enum
{
  RoomSensorCCTS,		// CCTS sensor
    RoomSensorCSM,		// wired/wireless sensor (should not use any more)
    RoomSensorCZCO,		// iSense sensor
    RoomSensorCRFS,		// wireless sensor
    RoomSensorCS,		// wired sensor
    RoomSensorNoSensor = 255
}RoomSensorType_t;

{
	"AirStreamDeviceUId": "001EC0028041", 
	"DeviceType": "ASH",
	"ZonesV2":{
			"Index": 0,				// zone index
			"Name": string,			// upto 16 chars including \0
			"ZoneType": int,		// zone type setting: ZoneType_e
			"SensType": int,		// zone sensor type: RoomSensorType_t
			"Mode": int,			// current zone mode: ZoneMode_e
			"Setpoint": int,		// current zone setpoint (x100)
			"Temp": int,			// current zone temperature (x100)
			"MaxAir": int,			// maximum damper open setting %
			"MinAir": int,			// minumum damper closed setting %
			"Const": int,			// constant number (each constant will have its own number)
			"ConstA": int			// constant zone active (zone forced open): 0 - not active, 1 - active
			"Master": int			// Master zone is forced open (display zone constant graphic in zone summary screen)
			"DmpFlt": int,			// zone damper motor fault
			"iSense": int,			// isense controller active: 
			
			// new
			"Area": int,			// area of zone in m2
			"Calibration": int,		// zone sensot calibration value
			"Bypass": int,			// constant zone set to bypass 
			"DmpPos": int,  		// current damper position
			"RfSignal": int,		// RF signal level: RfSignalLevel_e
			"BattVolt": int,		// battery level: BatteryLevel_e
			"SensorFault": int,		// sensor fault: 0 - no fault, 1- fault
			"BalanceMax":int,		// Zone balance max
			"BalanceMin":int,		// zone balance min
			"DamperSkip":int		// damper skip: 0 - no skip, 1 - skip
	}
}

// iZone V2 zone commands

// send all commands to /iZoneCommandV2

// configuration

// change zone balance max
{
	"BalanceMax":{
		"Index":a,
		"Max":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the balance max setting: adjustable in steps of 5% from 100 down to more than "BalanceMin"

// change zone balance min
{
	"BalanceMin":{
		"Index":a,
		"Min":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the balance min setting: adjustable in steps of 5% from 0 up to less than "BalanceMax"

// change damper skip
{
	"DamperSkip":{
		"Index":a,
		"Skip":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the skip setting: 0 - not skipped, 1 - skipped

// change zone name
{
	"ZoneName":{
		"Index":a,
		"Name":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - the new zone name

// change zone setting (sensor/control type)
{
	"ZoneSetting":{
		"Index":a,
		"Sensor":b,
		"Zone":c,
		"ConstNo":d
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the sensor type: RoomSensorType_t
// c - is the zone type: ZoneType_e
// d - in case zone is set to constant, the constant number needs to be set.

// change zone sensor calibration
{
	"SensorCalib":{
		"Index":a,
		"Calibrate":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the calibration value x10, value limits: 
// example:
// to set zone 1 sensor calibration to -0.5: -50 <= b <= 50
//{
//	"SensorCalib":{
//		"Index":1,
//		"Calibrate":-5
//	}
//}

// change zone bypass setting
{
	"ZoneBypass":{
		"Index":a,
		"Bypass":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the bypass setting: 0 - not bypass, 1 - zone used as bypass

// change zone area
{
	"ZoneArea":{
		"Index":a,
		"Area":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the zone area (m2), value limits 1 <= vb <= 255

// operation
// change zone mode
{
	"ZoneMode":{
		"Index":a,
		"Mode":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the new zone mode: ZoneMode_e

// change zone setpoint
{
	"ZoneSetpoint":{
		"Index":a,
		"Setpoint":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is the new setpoint x100, value limits: 1500 <= b <= 3000, steps of 50

// change zone max open
{
	"ZoneMaxAir":{
		"Index":a,
		"MaxAir":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is maximum open %, value limits 0 <= b <= 100, steps of 5

// change zone min open
{
	"ZoneMinAir":{
		"Index":a,
		"MinAir":b
	}
}
// where:
// a - is the zone number (0 = zone 1,...)
// b - is minimum open %, value limits 0 <= b <= 100, steps of 5

//*********************** Favourites JSON **********************************//
{
	"AirStreamDeviceUId": "001EC0028041", 
	"DeviceType": "ASH",
	"SchedulesV2": {
		"AirStreamDeviceUId":	"001EC0028041",		// system ID
		"Index":	int,			// index of favourite
		"Name": string,				// name of fav.
		"Active": int,				// true if schedule is active
		"Execute": "false",			// always read false comming from ASH, iPhone can set true, once read by ASH needs to be set to false (?)
		"Exists": "String",			// true/false, indicates whether the schedule exists (i.e. can be enabled)
		
		// new 
		"Start": int,				// hours*100+minutes, eg:1234 - 12:34
		"Stop": int,				// hours*100+minutes, eg:1234 - 12:34
		"M": int,					// Monday enabled flag, 0 - disabled, 1 - enabled
		"Tu": int,					// Tuesday enabled flag, 0 - disabled, 1 - enabled
		"W": int,					// Wednesday enabled flag, 0 - disabled, 1 - enabled
		"Th": int,					// Thursday enabled flag, 0 - disabled, 1 - enabled
		"F": int,					// Friday enabled flag, 0 - disabled, 1 - enabled
		"Sa": int,					// Saturday enabled flag, 0 - disabled, 1 - enabled
		"Su": int,					// Sunday enabled flag, 0 - disabled, 1 - enabled
		"Zones": [
			{
				"Sp": int,			// ZoneMode_Close, ZoneMode_Open else "Sp"*50 to get the setpoint int the x100 format
			},
			... 14 zones
		]
		
	}
}

// Favourites V2 commands
// change favourite name
{
	"SchedName":{
		"Index":a,
		"Name":b
	}
}
// where:
// a - is the zone number (0 = favourite 1,...)
// b - the new favourite name

// change favourite settings
{
	"SchedZones":{
		"Index":a,
		"Zones":[
		{"Mode":b,"Setpoint":c},
		... 14 zones
		]
	}
}
// where:
// a - is the zone number (0 = favourite 1,...)
// b - zone mode: ZoneMode_e
// c - zone setpoint x100, value limits: 1500 <= b <= 3000, steps of 50

// change schedule settings
{
	"SchedSettings":{
		"Index":a,
		"StartH":b,
		"StartM":c,
		"StopH":d,
		"StopM":e,
		"DaysEnabled":{
			"M":f,
			"Tu":g,
			"W":h,
			"Th":i,
			"F":j,
			"Sa":k,
			"Su":l
		}
	}
}
// where:
// a - is the zone number (0 = favourite 1,...)
// b - start hour (0-23), to disable send 31
// c - start minute (0-59), to disable send 63
// d - stop hour (0-23), to disable send 31
// e - stop minute (0-59), to disable send 63
// f - Moday enabled: 0 - disabled, 1 - enabled
// g - Tuesday enabled: 0 - disabled, 1 - enabled
// h - Wednesday enabled: 0 - disabled, 1 - enabled
// i - Thursday enabled: 0 - disabled, 1 - enabled
// j - Friday enabled: 0 - disabled, 1 - enabled
// k - Saturday enabled: 0 - disabled, 1 - enabled
// l - Friday enabled: 0 - disabled, 1 - enabled

// enable schdule
{
	"SchedEnable":{
		"Index":a,
		"Enabled":b
	}
}
// where:
// a - is the zone number (0 = favourite 1,...)
// b - schedule enabled: 0 - disabled, 1 - enabled

//********************** AC unit fault JSON *********************************//
{
	"AirStreamDeviceUId": "001EC0028041", 
	"DeviceType": "ASH",
	"AcUnitFaultHistV2": {
		"Faults":[
			{
				"Code": string,		// 4 letter string
				"D": int,			// day the fault occured
				"M": int,		// month the fault occured
				"Y": int,		// year the fault occured
				"H": int,		// hour the fault occured
				"M": int,		// minute the fault occured
				
			},
			... 11 faults
		]
	}
}


//******************** iZone device list JSON *******************************//
// This is the same as for the iLight system
{
	"AirStreamDeviceUId":	"001EC0028041",
	"DeviceType":	"ASH",
	"Fmw":	"string"					// where string is a comma separated string with device and their firmware version
}

//******************** Temperzone status JSON *******************************//
{
	"AirStreamDeviceUId": "001EC0028041", 
	"DeviceType": "ASH",
	"TemperzoneInfoV2": {
		"Temps":{
			"OutdoorCoil": int,
			"IndoorCoil": int,
			"Ambient": int,
			"SuctionLine": int,
			"DischargeLine": int,
			"DeIceSensor": int,
			"Evaporating": int,
			"Condensing": int,
			"Controller": int,
			"SuctionSideSuperheat": int,
			"DischargeSideSuperheat": int,
			"Vacant": int,
			"SuctionLinePressure": int,
			"DischargeLinePressure": int
		},
		"Outputs":{
			"OutdoorFanSpeed": int,
			"IndoorFanSpeed": int,
			"Exv1Position": int,
			"Exv2Position": int,
			"UnitCapacity": int,
			"UnitMode": int,
			"DigitalOutputs": int
		},
		"Thermostats":{
			"IndoorUnitCoilTemperature1": int,
			"IndoorUnitSuctionLineTemperature1": int,
			"IndoorUnitCoilTemperature2": int,
			"IndoorUnitSuctionLineTemperature2": int,
			"SupplyAirTemperature": int,
			"ReturnAirTemperature": int
		},
		"UC8":{
			"IdCode": int,
			"SoftwareVersion": int,
		},
		"History8":{
			"ModbusAddress": int,
			"Reserved1": int,
			"TotalRunningHours": int,
			"TotalRunningMinutes": int,
			"TotalCoolingCyclesMade": int,
			"TotalHeatingCyclesMade": int,
			"TotalDeiceCyclesMade": int,
			"HpTripEvents": int,
			"LpTripEvents": int,
			"FrostProtectionEvents": int,
			"FreezeProtectionEvents": int,
			"HighTemperatureProtectionEvents": int,
			"HighSuctionLineTemperatureProtectionEvents": int,
			"OverloadProtectionEvents": int,
			"LowDischanrgeSuperheatProtectionEvents": int,
			"HighDischanrgeSuperheatProtectionEvents": int,
			"NumberOfPowerOnResetEvents": int,
			"Reserved2": int,
			"Reserved3": int,
			"Reserved4": int,
			"IndoorCoilTemperatureSensorFaults": int,
			"OutdoorCoilTemperatureSensorFaults": int,
			"OutdoorAmbientTemperatureFaults": int,
			"DischargeLineTemperatureSensorFaults": int,
			"SuctionLineTemperatureSensorFaults": int,
			"DeiceTemperatureSensorFaults": int,
			"HighPressureSensorFaults": int,
			"LowPressureSensorFaults": int,
			"HighBoardTemperatureFaults": int,
			"ReverseCycleValveFaults": int,
			"IucCommunicationFaults": int,  
			"IucFaults": int,
			"InverterFaults": int,
			"CompressorOutOfEnvelopeFaults": int
		},
		"InputStatus":{
			"Inputs": int
		},
		"OutputStatus":{
			"Outputs": int
		},
		"Timers":{
			"MinimumOnOffTime": int,
			"MinimumOffOnTime": int,
			"MinimumOnOnTime": int
		}
	}
}



//**********************************************************
// request information
//**********************************************************
 // send all requests to /iZoneRequestV2
{
	"iZoneV2Request":
	{
		"Type": x,
		"No": y,
		"No1": z
	}
}
//where 
//x - data type to be requested. 
//	iZone system info 		- 1
//	iZone zone info			- 2
//  iZone favourite info	- 3
//  iZone AC unit fault list- 4
//  Temperzone unit status  - 5
//  iZone firmware list     - 6
//y - iZone zone number (if x==2)
//z - 

