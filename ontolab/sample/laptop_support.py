"""Authoring script for the sample ontology: IT customer service / laptop troubleshooting.

ALL CONTENT IS SYNTHETIC. Product names, procedures, articles and rules are invented
for demonstration and are not real vendor guidance.

Running this module writes data/ontology/laptop_support.yaml, which is what the
generic ingestion pipeline loads. The engine never imports this file.

    python -m ontolab.sample.laptop_support
"""

from __future__ import annotations

from pathlib import Path

import yaml

VERSION = "1.0.0"

# --------------------------------------------------------------------- schema

OBJECT_TYPES = [
    ("Product", "A laptop product line.", [("segment", "string", "Market segment.")]),
    ("DeviceModel", "A specific laptop model within a product line.", [
        ("release_year", "number", "Year the model was released."),
        ("removable_battery", "boolean", "Whether the customer can remove the battery without tools."),
        ("charger_wattage", "number", "Wattage of the bundled AC adapter."),
        ("usb_c_charging", "boolean", "Whether the model charges over USB-C."),
    ]),
    ("Component", "A hardware or software part of a laptop, or a subsystem grouping parts.", [
        ("kind", "string", "hardware, software or subsystem."),
        ("customer_replaceable", "boolean", "Whether the part can be shipped for the customer to fit."),
    ]),
    ("Symptom", "A problem as experienced by the customer. Organised in a hierarchy.", [
        ("level", "string", "category for grouping symptoms, specific for reportable ones."),
        ("severity", "string", "low, medium, high or safety."),
    ]),
    ("CallDriver", "The business reason a customer contacts support; used for reporting.", []),
    ("DiagnosticChecklist", "An ordered troubleshooting procedure for a symptom.", [
        ("owner", "string", "Team that maintains the checklist."),
    ]),
    ("DiagnosticAction", "A single reusable troubleshooting step.", [
        ("estimated_minutes", "number", "Typical time to perform the step."),
        ("risk", "string", "low, medium or high."),
        ("remote_capable", "boolean", "Whether the step can be done by the customer over the phone."),
        ("action_kind", "string", "check, fix, or disposition."),
    ]),
    ("ActionOutcome", "A possible result of performing a diagnostic action.", [
        ("code", "string", "Short outcome code, unique within the action."),
        ("resolution", "string", "resolved, continue, work_order or escalate."),
    ]),
    ("DecisionRule", "A business rule that decides whether an action may be performed.", [
        ("expression", "string", "Boolean expression over context facts."),
        ("category", "string", "safety, policy or prerequisite."),
        ("failure_message", "string", "What to tell the agent when the rule is not satisfied."),
    ]),
    ("KnowledgeArticle", "A support article describing how to perform actions.", [
        ("status", "string", "current, outdated or superseded."),
        ("last_reviewed", "string", "ISO date of the last editorial review."),
        ("summary", "string", "Key guidance in the article."),
        ("url", "string", "Synthetic link."),
    ]),
]

OPERATIONAL_TYPES = [
    ("ServiceCase", "A customer contact handled by support.", "service_case", "case_id"),
    ("WorkOrder", "A repair or part shipment raised from a case.", "work_order", "work_order_id"),
    ("DiagnosticExecution", "One diagnostic action performed during a case.", "diagnostic_execution", "execution_id"),
]

RELATIONSHIP_TYPES = [
    # name, description, sources, targets, hierarchical, edge properties
    ("HAS_MODEL", "A product line includes a device model.", ["Product"], ["DeviceModel"], False, []),
    ("HAS_COMPONENT", "A product contains a component.", ["Product"], ["Component"], False, []),
    ("PART_OF", "A component belongs to a subsystem.", ["Component"], ["Component"], True, []),
    ("SUBTYPE_OF", "A symptom is a more specific kind of another symptom.", ["Symptom"], ["Symptom"], True, []),
    ("INDICATES", "A symptom points to a component as a possible cause.", ["Symptom"], ["Component"], False,
     [("likelihood", "number", "Share of cases with this symptom traced to the component.")]),
    ("HAS_CALL_DRIVER", "A symptom is reported under a call driver.", ["Symptom"], ["CallDriver"], False, []),
    ("HAS_CHECKLIST", "A symptom is troubleshot with a checklist. Inherited by subtypes that have none.",
     ["Symptom"], ["DiagnosticChecklist"], False, []),
    ("CONTAINS", "A checklist contains an action at a step position.", ["DiagnosticChecklist"],
     ["DiagnosticAction"], False, [("step_order", "number", "Position of the action in the checklist.")]),
    ("HAS_OUTCOME", "An action can end in an outcome.", ["DiagnosticAction"], ["ActionOutcome"], False, []),
    ("LEADS_TO", "After an outcome, the next action to perform. May be conditional.", ["ActionOutcome"],
     ["DiagnosticAction"], False,
     [("condition", "string", "Expression over context (e.g. checklist_id) that must hold for this branch.")]),
    ("GOVERNED_BY", "An action may only be performed when a decision rule is satisfied.", ["DiagnosticAction"],
     ["DecisionRule"], False, []),
    ("DESCRIBES", "A knowledge article explains how to perform an action.", ["KnowledgeArticle"],
     ["DiagnosticAction"], False, []),
    ("SUPERSEDES", "A knowledge article replaces an older one.", ["KnowledgeArticle"], ["KnowledgeArticle"], False, []),
]

OPERATIONAL_RELATIONSHIPS = [
    ("HAS_SYMPTOM", "A service case was opened for a symptom.", ["ServiceCase"], ["Symptom"], "service_case", "case_id", "symptom_id"),
    ("FOR_MODEL", "A service case concerns a device model.", ["ServiceCase"], ["DeviceModel"], "service_case", "case_id", "device_model_id"),
    ("HAS_DRIVER", "A service case is classified under a call driver.", ["ServiceCase"], ["CallDriver"], "service_case", "case_id", "call_driver_id"),
    ("GENERATES", "A service case produced a work order.", ["ServiceCase"], ["WorkOrder"], "work_order", "case_id", "work_order_id"),
    ("EXECUTED", "A service case included a diagnostic execution.", ["ServiceCase"], ["DiagnosticExecution"], "diagnostic_execution", "case_id", "execution_id"),
    ("PERFORMED_ACTION", "A diagnostic execution performed an action.", ["DiagnosticExecution"], ["DiagnosticAction"], "diagnostic_execution", "execution_id", "action_id"),
    ("REPLACES_COMPONENT", "A work order replaces a component.", ["WorkOrder"], ["Component"], "work_order", "work_order_id", "component_id"),
]

# ------------------------------------------------------------------ instances

PRODUCTS = [
    ("P_AEROBOOK_PRO", "AeroBook Pro", "High-performance laptop line for creators and developers.", ["Pro line"], "premium"),
    ("P_AEROBOOK_AIR", "AeroBook Air", "Thin and light laptop line for everyday use.", ["Air line"], "consumer"),
    ("P_WORKMATE", "WorkMate", "Business laptop line with serviceable parts.", ["WorkMate business line"], "business"),
]
MODELS = [
    # id, name, product, year, removable battery, wattage, usb-c
    ("DM_PRO14", "AeroBook Pro 14 (2023)", "P_AEROBOOK_PRO", 2023, False, 100, True),
    ("DM_PRO16", "AeroBook Pro 16 (2024)", "P_AEROBOOK_PRO", 2024, False, 140, True),
    ("DM_AIR13", "AeroBook Air 13 (2023)", "P_AEROBOOK_AIR", 2023, False, 65, True),
    ("DM_WORK15", "WorkMate 15 (2022)", "P_WORKMATE", 2022, True, 65, False),
    ("DM_WORK14", "WorkMate 14 (2024)", "P_WORKMATE", 2024, True, 65, True),
]
COMPONENTS = [
    # id, name, definition, synonyms, kind, customer replaceable, parent
    ("C_POWER_SYSTEM", "Power System", "Parts that deliver and store electrical power.", ["power subsystem"], "subsystem", False, None),
    ("C_DISPLAY_SYSTEM", "Display System", "Parts that produce the picture on the built-in screen.", ["video subsystem"], "subsystem", False, None),
    ("C_INPUT_SYSTEM", "Input System", "Parts the user types and points with.", ["input devices"], "subsystem", False, None),
    ("C_WIRELESS_SYSTEM", "Wireless System", "Parts that provide wireless networking.", ["wireless subsystem"], "subsystem", False, None),
    ("C_CORE_SYSTEM", "Core System", "Main board, memory and cooling.", ["core hardware"], "subsystem", False, None),
    ("C_SOFTWARE", "Software and Firmware", "Operating system, drivers and firmware.", ["software stack"], "subsystem", False, None),
    ("C_BATTERY", "Battery", "Rechargeable battery pack.", ["battery pack", "cell pack"], "hardware", False, "C_POWER_SYSTEM"),
    ("C_AC_ADAPTER", "AC Adapter", "External power supply and its cable.", ["charger", "power brick", "power adapter"], "hardware", True, "C_POWER_SYSTEM"),
    ("C_CHARGING_PORT", "Charging Port", "The DC-in or USB-C port that receives power.", ["DC jack", "power port"], "hardware", False, "C_POWER_SYSTEM"),
    ("C_DISPLAY_PANEL", "Display Panel", "The built-in LCD or OLED screen.", ["LCD", "screen panel"], "hardware", False, "C_DISPLAY_SYSTEM"),
    ("C_DISPLAY_CABLE", "Display Cable", "Internal cable between main board and panel.", ["eDP cable", "video cable"], "hardware", False, "C_DISPLAY_SYSTEM"),
    ("C_GPU", "Graphics Processor", "Chip that renders the picture.", ["GPU", "graphics card", "video chip"], "hardware", False, "C_DISPLAY_SYSTEM"),
    ("C_KEYBOARD", "Keyboard", "Built-in keyboard assembly.", ["keyboard assembly", "keys"], "hardware", False, "C_INPUT_SYSTEM"),
    ("C_KEYBOARD_CABLE", "Keyboard Ribbon Cable", "Flat cable connecting keyboard and main board.", ["keyboard flex cable"], "hardware", False, "C_INPUT_SYSTEM"),
    ("C_TOUCHPAD", "Touchpad", "Built-in pointing surface.", ["trackpad"], "hardware", False, "C_INPUT_SYSTEM"),
    ("C_WIFI_CARD", "Wireless Card", "Module providing Wi-Fi and Bluetooth.", ["WLAN card", "Wi-Fi module", "network adapter"], "hardware", False, "C_WIRELESS_SYSTEM"),
    ("C_ANTENNA", "Wireless Antenna", "Antenna leads routed through the lid.", ["Wi-Fi antenna"], "hardware", False, "C_WIRELESS_SYSTEM"),
    ("C_MOTHERBOARD", "Main Board", "Primary circuit board.", ["motherboard", "logic board", "system board"], "hardware", False, "C_CORE_SYSTEM"),
    ("C_RAM", "Memory Module", "Installed system memory.", ["RAM", "memory"], "hardware", True, "C_CORE_SYSTEM"),
    ("C_COOLING_FAN", "Cooling Fan", "Fan and heat sink assembly.", ["fan", "thermal module"], "hardware", False, "C_CORE_SYSTEM"),
    ("C_FIRMWARE", "System Firmware", "BIOS/UEFI and embedded controller firmware.", ["BIOS", "UEFI", "embedded controller firmware"], "software", False, "C_SOFTWARE"),
    ("C_OS_SOFTWARE", "Operating System and Drivers", "Operating system, device drivers and settings.", ["OS", "drivers", "device drivers"], "software", False, "C_SOFTWARE"),
]
CALL_DRIVERS = [
    ("CD_POWER", "Power and Charging", "Contacts about the laptop not powering on or not charging.", ["power & charging"]),
    ("CD_BATTERY", "Battery Health and Life", "Contacts about battery condition, runtime or safety.", ["battery"]),
    ("CD_DISPLAY", "Display", "Contacts about the built-in screen.", ["screen", "video"]),
    ("CD_INPUT", "Keyboard and Input", "Contacts about keyboard or touchpad.", ["input devices"]),
    ("CD_WIRELESS", "Wireless Connectivity", "Contacts about Wi-Fi connectivity.", ["networking", "Wi-Fi"]),
]
SYMPTOMS = [
    # id, name, definition, synonyms, level, severity, parent, call driver, checklist, {component: likelihood}
    ("S_POWER", "Power Issue", "Any problem with powering on, staying on or charging.", ["power problem"], "category", "medium", None, "CD_POWER", None, {}),
    ("S_NOT_CHARGING", "Laptop Not Charging", "The battery does not charge when the adapter is connected.",
     ["not charging", "battery not charging", "won't charge", "charging failure"], "specific", "medium", "S_POWER", "CD_POWER", "CL_NOT_CHARGING",
     {"C_AC_ADAPTER": 0.45, "C_CHARGING_PORT": 0.2, "C_BATTERY": 0.2, "C_MOTHERBOARD": 0.1, "C_FIRMWARE": 0.05}),
    ("S_CHARGING_INTERMITTENT", "Charges Intermittently", "Charging starts and stops, often when the cable is moved.",
     ["charging cuts in and out", "loose charger", "intermittent charging"], "specific", "medium", "S_NOT_CHARGING", "CD_POWER", None,
     {"C_AC_ADAPTER": 0.4, "C_CHARGING_PORT": 0.4, "C_BATTERY": 0.2}),
    ("S_PLUGGED_NOT_CHARGING", "Plugged In, Not Charging", "The system reports the adapter is connected but the charge level does not rise.",
     ["plugged in not charging", "stuck at a percentage"], "specific", "medium", "S_NOT_CHARGING", "CD_POWER", None,
     {"C_BATTERY": 0.4, "C_FIRMWARE": 0.3, "C_AC_ADAPTER": 0.3}),
    ("S_NO_POWER", "Laptop Won't Power On", "No response when the power button is pressed.",
     ["no power", "won't turn on", "does not start", "power failure"], "specific", "high", "S_POWER", "CD_POWER", "CL_NO_POWER",
     {"C_AC_ADAPTER": 0.3, "C_MOTHERBOARD": 0.3, "C_BATTERY": 0.25, "C_RAM": 0.15}),
    ("S_UNEXPECTED_SHUTDOWN", "Unexpected Shutdown", "The laptop turns off by itself during use.",
     ["random shutdown", "turns off by itself", "shuts down suddenly"], "specific", "high", "S_POWER", "CD_POWER", "CL_BATTERY",
     {"C_BATTERY": 0.4, "C_COOLING_FAN": 0.3, "C_MOTHERBOARD": 0.2, "C_OS_SOFTWARE": 0.1}),
    ("S_BATTERY", "Battery Issue", "Any problem with battery condition or runtime.", ["battery problem"], "category", "medium", "S_POWER", "CD_BATTERY", "CL_BATTERY", {}),
    ("S_BATTERY_DRAIN", "Battery Drains Quickly", "Runtime on battery is much shorter than expected.",
     ["short battery life", "battery dies fast", "poor battery life"], "specific", "low", "S_BATTERY", "CD_BATTERY", None,
     {"C_BATTERY": 0.6, "C_OS_SOFTWARE": 0.3, "C_FIRMWARE": 0.1}),
    ("S_BATTERY_SWOLLEN", "Swollen Battery", "The battery has expanded, lifting the keyboard, touchpad or base.",
     ["bulging battery", "battery expanding", "case is bulging"], "specific", "safety", "S_BATTERY", "CD_BATTERY", "CL_SWOLLEN_BATTERY",
     {"C_BATTERY": 1.0}),
    ("S_BATTERY_NOT_DETECTED", "Battery Not Detected", "The system reports that no battery is present.",
     ["no battery detected", "battery missing"], "specific", "medium", "S_BATTERY", "CD_BATTERY", None,
     {"C_BATTERY": 0.6, "C_MOTHERBOARD": 0.2, "C_FIRMWARE": 0.2}),
    ("S_DISPLAY", "Display Issue", "Any problem with the picture on the built-in screen.", ["screen problem", "display problem"], "category", "medium", None, "CD_DISPLAY", "CL_DISPLAY_QUALITY", {}),
    ("S_BLACK_SCREEN", "Black Screen", "The laptop is powered on but the built-in screen shows no picture.",
     ["blank screen", "no display", "screen stays black", "no picture"], "specific", "high", "S_DISPLAY", "CD_DISPLAY", "CL_BLACK_SCREEN",
     {"C_DISPLAY_PANEL": 0.3, "C_DISPLAY_CABLE": 0.2, "C_GPU": 0.15, "C_RAM": 0.15, "C_MOTHERBOARD": 0.1, "C_OS_SOFTWARE": 0.1}),
    ("S_DISPLAY_FAILURE", "Display Failure", "The built-in panel is physically or electrically damaged: cracked glass or a dead backlight.",
     ["broken screen", "cracked screen", "backlight failure"], "specific", "high", "S_DISPLAY", "CD_DISPLAY", None,
     {"C_DISPLAY_PANEL": 0.8, "C_DISPLAY_CABLE": 0.2}),
    ("S_DISPLAY_FLICKER", "Screen Flickering", "The picture flickers or flashes.",
     ["flickering display", "screen flashes"], "specific", "medium", "S_DISPLAY", "CD_DISPLAY", None,
     {"C_DISPLAY_CABLE": 0.4, "C_OS_SOFTWARE": 0.3, "C_GPU": 0.2, "C_DISPLAY_PANEL": 0.1}),
    ("S_DISPLAY_LINES", "Lines or Artifacts on Screen", "Vertical or horizontal lines, blocks or discolouration appear.",
     ["lines on screen", "screen artifacts", "distorted picture"], "specific", "medium", "S_DISPLAY", "CD_DISPLAY", None,
     {"C_DISPLAY_PANEL": 0.5, "C_DISPLAY_CABLE": 0.3, "C_GPU": 0.2}),
    ("S_INPUT", "Input Device Issue", "Any problem with the keyboard or touchpad.", ["input problem"], "category", "medium", None, "CD_INPUT", None, {}),
    ("S_KEYBOARD_FAILURE", "Keyboard Not Working", "The built-in keyboard does not respond.",
     ["keyboard failure", "keyboard dead", "cannot type"], "specific", "medium", "S_INPUT", "CD_INPUT", "CL_KEYBOARD",
     {"C_KEYBOARD": 0.5, "C_OS_SOFTWARE": 0.3, "C_KEYBOARD_CABLE": 0.2}),
    ("S_KEYS_STUCK", "Some Keys Not Working", "Individual keys do not register or stick.",
     ["sticky keys", "dead keys", "some keys do not work"], "specific", "low", "S_KEYBOARD_FAILURE", "CD_INPUT", None,
     {"C_KEYBOARD": 0.9, "C_OS_SOFTWARE": 0.1}),
    ("S_KEYBOARD_BACKLIGHT", "Keyboard Backlight Not Working", "The keys do not light up.",
     ["backlit keyboard off", "keyboard lights not working"], "specific", "low", "S_KEYBOARD_FAILURE", "CD_INPUT", None,
     {"C_KEYBOARD": 0.5, "C_FIRMWARE": 0.3, "C_OS_SOFTWARE": 0.2}),
    ("S_TOUCHPAD", "Touchpad Not Working", "The pointer does not move or clicks are not registered.",
     ["trackpad not working", "cursor frozen"], "specific", "medium", "S_INPUT", "CD_INPUT", None,
     {"C_TOUCHPAD": 0.6, "C_OS_SOFTWARE": 0.4}),
    ("S_CONNECTIVITY", "Connectivity Issue", "Any problem connecting to a network.", ["network problem"], "category", "medium", None, "CD_WIRELESS", None, {}),
    ("S_WIFI", "Wi-Fi Connectivity Problem", "The laptop cannot use Wi-Fi reliably.",
     ["wifi problem", "wireless problem", "wifi not working"], "specific", "medium", "S_CONNECTIVITY", "CD_WIRELESS", "CL_WIFI", {}),
    ("S_WIFI_NOT_DETECTED", "No Wi-Fi Networks Found", "No wireless networks are listed or the wireless adapter is missing.",
     ["wifi adapter missing", "cannot see any networks"], "specific", "medium", "S_WIFI", "CD_WIRELESS", None,
     {"C_WIFI_CARD": 0.6, "C_OS_SOFTWARE": 0.3, "C_FIRMWARE": 0.1}),
    ("S_WIFI_DROPS", "Wi-Fi Keeps Disconnecting", "The wireless connection drops repeatedly.",
     ["wifi drops", "intermittent wifi", "wifi keeps cutting out"], "specific", "medium", "S_WIFI", "CD_WIRELESS", None,
     {"C_WIFI_CARD": 0.35, "C_ANTENNA": 0.35, "C_OS_SOFTWARE": 0.3}),
    ("S_WIFI_SLOW", "Slow Wi-Fi", "Wireless speeds are far below what other devices get.",
     ["slow wireless", "wifi is slow"], "specific", "low", "S_WIFI", "CD_WIRELESS", None,
     {"C_ANTENNA": 0.4, "C_WIFI_CARD": 0.3, "C_OS_SOFTWARE": 0.3}),
]

FIX = [("FIXED", "Problem resolved", "resolved"), ("NO_CHANGE", "No change", "continue")]
OUTCOME_SYNONYMS = {"FIXED": ["fixed", "worked", "resolved"], "NO_CHANGE": ["failed", "did not help", "not fixed", "still not working"]}

ACTIONS = [
    # id, name, definition, synonyms, minutes, risk, remote, kind, outcomes
    ("A_CHECK_POWER_LED", "Check Power and Charging LEDs", "Observe the power and charging indicator lights with the adapter connected.",
     ["check indicator lights", "LED check"], 2, "low", True, "check",
     [("LED_ON", "An indicator LED is lit", "continue"), ("LED_OFF", "No indicator LED activity", "continue")]),
    ("A_VERIFY_ADAPTER", "Verify AC Adapter and Cable", "Confirm the adapter is the correct wattage, undamaged and firmly connected at both ends.",
     ["check the charger", "adapter check"], 3, "low", True, "check",
     [("ADAPTER_OK", "Adapter is correct and undamaged", "continue"), ("ADAPTER_FAULTY", "Adapter is damaged, wrong wattage or shows no light", "continue")]),
    ("A_TRY_OTHER_OUTLET", "Try a Different Wall Outlet", "Connect the adapter directly to another wall outlet, without a power strip.",
     ["different socket", "bypass power strip"], 2, "low", True, "fix", FIX),
    ("A_INSPECT_PORT", "Inspect Charging Port", "Look into the charging port for debris, bent pins or looseness.",
     ["check the charging port", "port inspection"], 3, "low", True, "check",
     [("PORT_CLEAN", "Port is clean and firm", "continue"), ("PORT_DAMAGED", "Port is damaged, loose or obstructed", "continue")]),
    ("A_POWER_RESET", "Perform Power Reset", "Disconnect the adapter and peripherals, then hold the power button to drain residual charge.",
     ["hard reset", "power drain", "static discharge", "EC reset"], 4, "low", True, "fix", FIX),
    ("A_TRY_KNOWN_GOOD_ADAPTER", "Test with Known-Good Adapter", "Charge with a second adapter that is known to work.",
     ["swap the charger", "spare adapter test"], 5, "low", True, "check",
     [("CHARGES", "Charges with the other adapter", "continue"), ("STILL_NOT_CHARGING", "Does not charge with the other adapter", "continue")]),
    ("A_CHECK_BATTERY_HEALTH", "Run Battery Health Check", "Run the built-in battery diagnostic and read the health percentage.",
     ["battery report", "battery diagnostics"], 6, "low", True, "check",
     [("HEALTHY", "Battery health is acceptable", "continue"), ("DEGRADED", "Battery is degraded or fails diagnostics", "continue")]),
    ("A_BIOS_UPDATE", "Update BIOS and Firmware", "Install the latest system firmware.",
     ["firmware update", "flash the BIOS", "UEFI update"], 15, "medium", True, "fix", FIX),
    ("A_RESEAT_BATTERY", "Reseat Removable Battery", "Remove the battery, wait, and fit it again.",
     ["remove and refit battery"], 5, "medium", True, "fix", FIX),
    ("A_RESEAT_RAM", "Reseat Memory Modules", "Open the service cover and reseat the memory modules.",
     ["reseat RAM", "refit memory"], 12, "high", True, "fix", FIX),
    ("A_REDUCE_POWER_USAGE", "Review Power Settings and Apps", "Identify apps and settings that drain the battery and adjust them.",
     ["power plan review", "battery usage review"], 8, "low", True, "fix", FIX),
    ("A_BATTERY_CALIBRATION", "Calibrate Battery", "Fully charge, fully discharge and recharge so the gauge is accurate.",
     ["battery recalibration", "gauge reset"], 10, "low", True, "fix", FIX),
    ("A_STOP_USING_DEVICE", "Stop Using and Power Down Device", "Instruct the customer to shut down, unplug and stop using the device.",
     ["safety stop", "power down immediately"], 2, "low", True, "disposition",
     [("CONFIRMED", "Customer has powered down and unplugged the device", "continue")]),
    ("A_BRIGHTNESS_CHECK", "Check Brightness and Display Toggle", "Raise the brightness and press the display-switch key.",
     ["brightness check", "display toggle key"], 2, "low", True, "fix", FIX),
    ("A_EXTERNAL_MONITOR_TEST", "Test with External Monitor", "Connect an external monitor to see whether the picture appears there.",
     ["external display test", "second screen test"], 6, "low", True, "check",
     [("EXT_OK", "External monitor displays correctly", "continue"), ("EXT_SAME_ISSUE", "External monitor shows the same problem", "continue")]),
    ("A_DISPLAY_SELF_TEST", "Run Built-in Display Self-Test", "Start the panel self-test, which shows colour bars without the operating system.",
     ["LCD self test", "panel BIST"], 4, "low", True, "check",
     [("PASSES", "Self-test shows clean colour bars", "continue"), ("FAILS", "Self-test is blank or shows defects", "continue")]),
    ("A_GPU_DRIVER_UPDATE", "Update or Roll Back Graphics Driver", "Install the current graphics driver, or roll back a recent one.",
     ["graphics driver reinstall", "video driver update"], 10, "low", True, "fix", FIX),
    ("A_REBOOT", "Restart the Laptop", "Perform a normal restart.", ["reboot", "restart"], 3, "low", True, "fix", FIX),
    ("A_CHECK_FILTER_KEYS", "Check Keyboard Accessibility Settings", "Turn off Filter Keys and Sticky Keys and check the keyboard layout.",
     ["filter keys check", "keyboard settings check"], 3, "low", True, "fix", FIX),
    ("A_EXTERNAL_KEYBOARD_TEST", "Test with External Keyboard", "Connect a USB keyboard to see whether typing works.",
     ["USB keyboard test"], 4, "low", True, "check",
     [("EXT_KB_WORKS", "External keyboard works", "continue"), ("EXT_KB_FAILS", "External keyboard also fails", "continue")]),
    ("A_BIOS_KEY_TEST", "Test Keys in BIOS Setup", "Enter firmware setup and test the keys outside the operating system.",
     ["pre-boot key test"], 5, "low", True, "check",
     [("KEYS_WORK", "Keys work outside the operating system", "continue"), ("KEYS_FAIL", "Keys fail outside the operating system", "continue")]),
    ("A_KEYBOARD_DRIVER_REINSTALL", "Reinstall Keyboard Driver", "Remove the keyboard device driver and let the system reinstall it.",
     ["keyboard driver refresh"], 6, "low", True, "fix", FIX),
    ("A_CLEAN_KEYBOARD", "Clean Keyboard with Compressed Air", "Blow debris from under the keys with compressed air.",
     ["keyboard cleaning"], 6, "medium", True, "fix", FIX),
    ("A_TOGGLE_WIFI", "Toggle Wi-Fi and Airplane Mode", "Turn airplane mode on and off and confirm Wi-Fi is enabled.",
     ["wifi switch check", "airplane mode toggle"], 2, "low", True, "fix", FIX),
    ("A_OTHER_DEVICE_TEST", "Check Other Devices on the Same Network", "See whether another device connects to the same network.",
     ["network isolation test"], 3, "low", True, "check",
     [("OTHERS_OK", "Other devices connect normally", "continue"), ("OTHERS_FAIL", "Other devices also fail", "continue")]),
    ("A_RESTART_ROUTER", "Restart Router and Modem", "Power-cycle the router and modem.", ["router reboot", "power-cycle the router"], 6, "low", True, "fix", FIX),
    ("A_FORGET_REJOIN", "Forget and Rejoin the Network", "Remove the saved network and connect again with the password.",
     ["forget network", "re-add the network"], 4, "low", True, "fix", FIX),
    ("A_WIFI_DRIVER_UPDATE", "Update Wireless Driver", "Install the current wireless adapter driver.", ["WLAN driver update", "network driver update"], 8, "low", True, "fix", FIX),
    ("A_NETWORK_RESET", "Reset Network Settings", "Reset the operating system's network stack to defaults.", ["network stack reset"], 7, "medium", True, "fix", FIX),
    ("A_WIFI_DIAG", "Run Wireless Hardware Diagnostics", "Run the built-in wireless module hardware test.", ["WLAN hardware test"], 6, "low", True, "check",
     [("PASSES", "Wireless hardware passes", "continue"), ("FAILS", "Wireless hardware fails", "continue")]),
    ("A_SHIP_PART", "Ship Customer-Replaceable Part", "Send a replacement part the customer can fit themselves.",
     ["send a replacement part", "CRU shipment"], 6, "low", True, "disposition",
     [("PART_SHIPPED", "Replacement part ordered", "work_order")]),
    ("A_DISPATCH_REPAIR", "Create Depot Repair Work Order", "Arrange for the laptop to be sent to the repair depot.",
     ["send in for repair", "depot repair", "mail-in repair"], 8, "low", True, "disposition",
     [("WO_CREATED", "Repair work order created", "work_order"), ("DECLINED", "Customer declines repair", "continue")]),
    ("A_ESCALATE_L2", "Escalate to Tier 2 Support", "Hand the case to the second-level support team.",
     ["tier 2 escalation", "level 2 handoff"], 5, "low", True, "disposition",
     [("ESCALATED", "Case handed to tier 2", "escalate")]),
]

CHECKLISTS = [
    ("CL_NOT_CHARGING", "Not Charging Checklist", "Procedure for a laptop that does not charge.", "Power Team",
     ["A_CHECK_POWER_LED", "A_VERIFY_ADAPTER", "A_TRY_OTHER_OUTLET", "A_INSPECT_PORT", "A_POWER_RESET",
      "A_TRY_KNOWN_GOOD_ADAPTER", "A_CHECK_BATTERY_HEALTH", "A_BIOS_UPDATE", "A_DISPATCH_REPAIR"]),
    ("CL_NO_POWER", "No Power Checklist", "Procedure for a laptop that does not power on.", "Power Team",
     ["A_CHECK_POWER_LED", "A_VERIFY_ADAPTER", "A_POWER_RESET", "A_RESEAT_BATTERY", "A_RESEAT_RAM", "A_DISPATCH_REPAIR"]),
    ("CL_BATTERY", "Battery Checklist", "Procedure for battery health, runtime and shutdown problems.", "Power Team",
     ["A_CHECK_BATTERY_HEALTH", "A_REDUCE_POWER_USAGE", "A_BATTERY_CALIBRATION", "A_BIOS_UPDATE", "A_DISPATCH_REPAIR"]),
    ("CL_SWOLLEN_BATTERY", "Swollen Battery Safety Checklist", "Safety procedure for a swollen battery.", "Safety Team",
     ["A_STOP_USING_DEVICE", "A_DISPATCH_REPAIR"]),
    ("CL_BLACK_SCREEN", "Black Screen Checklist", "Procedure for a powered-on laptop with no picture.", "Display Team",
     ["A_CHECK_POWER_LED", "A_BRIGHTNESS_CHECK", "A_POWER_RESET", "A_EXTERNAL_MONITOR_TEST", "A_DISPLAY_SELF_TEST",
      "A_GPU_DRIVER_UPDATE", "A_RESEAT_RAM", "A_DISPATCH_REPAIR"]),
    ("CL_DISPLAY_QUALITY", "Display Quality Checklist", "Procedure for flicker, lines and other picture defects.", "Display Team",
     ["A_EXTERNAL_MONITOR_TEST", "A_GPU_DRIVER_UPDATE", "A_DISPLAY_SELF_TEST", "A_DISPATCH_REPAIR"]),
    ("CL_KEYBOARD", "Keyboard Checklist", "Procedure for an unresponsive keyboard or keys.", "Input Team",
     ["A_REBOOT", "A_CHECK_FILTER_KEYS", "A_EXTERNAL_KEYBOARD_TEST", "A_BIOS_KEY_TEST", "A_KEYBOARD_DRIVER_REINSTALL",
      "A_CLEAN_KEYBOARD", "A_DISPATCH_REPAIR"]),
    ("CL_WIFI", "Wi-Fi Checklist", "Procedure for wireless connectivity problems.", "Network Team",
     ["A_TOGGLE_WIFI", "A_OTHER_DEVICE_TEST", "A_RESTART_ROUTER", "A_FORGET_REJOIN", "A_WIFI_DRIVER_UPDATE",
      "A_NETWORK_RESET", "A_WIFI_DIAG", "A_DISPATCH_REPAIR"]),
]

# Where an outcome does NOT simply lead to the next step of the checklist.
# (checklist or "*", action, outcome code) -> next action, or list of (extra condition, next action)
BATTERY_REPLACEMENT = [("removable_battery == true", "A_SHIP_PART"), ("removable_battery != true", "A_DISPATCH_REPAIR")]
TRANSITIONS = {
    ("*", "A_VERIFY_ADAPTER", "ADAPTER_FAULTY"): "A_SHIP_PART",
    ("*", "A_INSPECT_PORT", "PORT_DAMAGED"): "A_DISPATCH_REPAIR",
    ("*", "A_TRY_KNOWN_GOOD_ADAPTER", "CHARGES"): "A_SHIP_PART",
    ("*", "A_CHECK_BATTERY_HEALTH", "DEGRADED"): BATTERY_REPLACEMENT,
    ("*", "A_DISPATCH_REPAIR", "DECLINED"): "A_ESCALATE_L2",
    ("CL_NO_POWER", "A_CHECK_POWER_LED", "LED_ON"): "A_POWER_RESET",
    ("CL_BLACK_SCREEN", "A_CHECK_POWER_LED", "LED_OFF"): "A_VERIFY_ADAPTER",
    ("CL_BLACK_SCREEN", "A_EXTERNAL_MONITOR_TEST", "EXT_SAME_ISSUE"): "A_RESEAT_RAM",
    ("CL_BLACK_SCREEN", "A_DISPLAY_SELF_TEST", "FAILS"): "A_DISPATCH_REPAIR",
    ("CL_BLACK_SCREEN", "A_GPU_DRIVER_UPDATE", "NO_CHANGE"): "A_DISPATCH_REPAIR",
    ("CL_DISPLAY_QUALITY", "A_EXTERNAL_MONITOR_TEST", "EXT_OK"): "A_DISPLAY_SELF_TEST",
    ("CL_DISPLAY_QUALITY", "A_GPU_DRIVER_UPDATE", "NO_CHANGE"): "A_ESCALATE_L2",
    ("CL_DISPLAY_QUALITY", "A_DISPLAY_SELF_TEST", "PASSES"): "A_GPU_DRIVER_UPDATE",
    ("CL_KEYBOARD", "A_EXTERNAL_KEYBOARD_TEST", "EXT_KB_FAILS"): "A_KEYBOARD_DRIVER_REINSTALL",
    ("CL_KEYBOARD", "A_BIOS_KEY_TEST", "KEYS_FAIL"): "A_CLEAN_KEYBOARD",
    ("CL_WIFI", "A_OTHER_DEVICE_TEST", "OTHERS_OK"): "A_FORGET_REJOIN",
    ("CL_WIFI", "A_RESTART_ROUTER", "NO_CHANGE"): "A_ESCALATE_L2",
    ("CL_WIFI", "A_WIFI_DIAG", "PASSES"): "A_ESCALATE_L2",
}

RULES = [
    # id, name, definition, expression, category, message, governed actions
    ("R_BIOS_POWER", "Firmware Update Power Requirement", "A firmware update must not lose power part-way through.",
     "ac_connected == true and battery_percent >= 20", "safety",
     "Connect the AC adapter and charge to at least 20% before updating firmware.", ["A_BIOS_UPDATE"]),
    ("R_OPEN_CHASSIS", "Internal Access Safety Requirement", "Internal parts may only be handled with the device off and unplugged, by a capable customer.",
     "device_powered_off == true and ac_connected == false and customer_technical == true", "safety",
     "Do not guide the customer inside the device unless it is off, unplugged and they are comfortable doing so.",
     ["A_RESEAT_RAM", "A_RESEAT_BATTERY"]),
    ("R_REMOVABLE_BATTERY", "Removable Battery Requirement", "Only models with a customer-removable battery allow a battery reseat.",
     "removable_battery == true", "prerequisite", "This model's battery is not customer-removable.", ["A_RESEAT_BATTERY"]),
    ("R_NO_SWOLLEN_BATTERY", "Swollen Battery Prohibition", "Never charge, discharge or stress a swollen battery.",
     "battery_swollen != true", "safety", "Battery is swollen: stop, power down and arrange repair.",
     ["A_POWER_RESET", "A_BATTERY_CALIBRATION", "A_TRY_KNOWN_GOOD_ADAPTER", "A_CHECK_BATTERY_HEALTH"]),
    ("R_POWERED_OFF_CLEANING", "Power-Off Cleaning Requirement", "Clean the keyboard only with the device powered off.",
     "device_powered_off == true", "safety", "Power the device off before cleaning.", ["A_CLEAN_KEYBOARD"]),
    ("R_ADMIN_RIGHTS", "Administrator Rights Requirement", "Driver, firmware and network changes need administrator rights.",
     "has_admin_rights == true", "prerequisite", "The customer needs administrator rights on the device.",
     ["A_BIOS_UPDATE", "A_GPU_DRIVER_UPDATE", "A_KEYBOARD_DRIVER_REINSTALL", "A_WIFI_DRIVER_UPDATE", "A_NETWORK_RESET"]),
    ("R_WARRANTY_REPAIR", "Repair Entitlement Policy", "Depot repair needs warranty cover or agreement to pay.",
     "in_warranty == true or customer_accepts_paid_repair == true", "policy",
     "Out of warranty: the customer must agree to a paid repair first.", ["A_DISPATCH_REPAIR"]),
    ("R_CRU_ENTITLEMENT", "Part Shipment Entitlement Policy", "Only customer-replaceable parts under warranty are shipped.",
     "in_warranty == true and part_is_customer_replaceable == true", "policy",
     "Part shipment needs warranty cover and a customer-replaceable part.", ["A_SHIP_PART"]),
]

ARTICLES = [
    # id, title, summary, status, last reviewed, described actions
    ("KA_1001", "Checking Your AC Adapter", "Match the wattage on the adapter label to the model; inspect the cable for kinks.", "current", "2025-06-10", ["A_VERIFY_ADAPTER", "A_TRY_KNOWN_GOOD_ADAPTER"]),
    ("KA_1012", "Power Reset Procedure (2021)", "Unplug the adapter and hold the power button for 10 seconds.", "superseded", "2021-03-02", ["A_POWER_RESET"]),
    ("KA_1044", "Power Reset Procedure", "Unplug the adapter and all peripherals, then hold the power button for 30 seconds.", "current", "2025-08-21", ["A_POWER_RESET"]),
    ("KA_1020", "Inspecting the Charging Port", "Use a torch; never insert metal objects into the port.", "current", "2025-02-14", ["A_INSPECT_PORT"]),
    ("KA_1031", "Reading the Battery Health Report", "Health below 60% or a failed diagnostic means the battery should be replaced.", "current", "2025-05-30", ["A_CHECK_BATTERY_HEALTH", "A_BATTERY_CALIBRATION"]),
    ("KA_1050", "Swollen Battery Safety Bulletin", "Stop using the device, do not charge it, and do not press on the case.", "current", "2025-09-01", ["A_STOP_USING_DEVICE"]),
    ("KA_1060", "Updating System Firmware", "Use the built-in updater with the adapter connected; do not interrupt the update.", "current", "2025-07-18", ["A_BIOS_UPDATE"]),
    ("KA_1061", "Updating Firmware from a USB Drive", "Create a bootable USB drive with the legacy flashing tool.", "outdated", "2020-11-05", ["A_BIOS_UPDATE"]),
    ("KA_1070", "Testing with an External Monitor", "Use HDMI or USB-C and press the display-switch key after connecting.", "current", "2025-04-08", ["A_EXTERNAL_MONITOR_TEST"]),
    ("KA_1071", "Running the Display Self-Test", "Hold the self-test key while powering on; colour bars should fill the panel.", "current", "2025-04-08", ["A_DISPLAY_SELF_TEST"]),
    ("KA_1080", "Rolling Back a Graphics Driver", "Use Device Manager to roll back, then pause driver updates for a week.", "current", "2025-03-27", ["A_GPU_DRIVER_UPDATE"]),
    ("KA_1090", "Keyboard Troubleshooting Basics", "Rule out settings and software before suspecting hardware.", "current", "2025-01-19", ["A_EXTERNAL_KEYBOARD_TEST", "A_BIOS_KEY_TEST", "A_CHECK_FILTER_KEYS"]),
    ("KA_1100", "Wi-Fi Troubleshooting Basics", "Check the wireless switch, then the network, then the laptop.", "current", "2025-06-02", ["A_TOGGLE_WIFI", "A_FORGET_REJOIN", "A_RESTART_ROUTER"]),
    ("KA_1101", "Resetting Network Settings", "A network reset removes saved networks and VPN profiles.", "current", "2025-06-02", ["A_NETWORK_RESET", "A_WIFI_DRIVER_UPDATE"]),
    ("KA_1110", "Depot Repair and Part Shipment Policy", "Depot repair takes 5-7 business days; customer-replaceable parts ship next day.", "current", "2025-10-01", ["A_DISPATCH_REPAIR", "A_SHIP_PART"]),
    ("KA_1120", "Reseating Memory Modules", "Power off, unplug, and ground yourself before touching memory.", "current", "2025-02-25", ["A_RESEAT_RAM"]),
]
SUPERSEDES = [("KA_1044", "KA_1012")]

PRODUCT_COMPONENTS = {
    "P_AEROBOOK_PRO": ["C_BATTERY", "C_AC_ADAPTER", "C_CHARGING_PORT", "C_DISPLAY_PANEL", "C_DISPLAY_CABLE", "C_GPU",
                       "C_KEYBOARD", "C_KEYBOARD_CABLE", "C_TOUCHPAD", "C_WIFI_CARD", "C_ANTENNA", "C_MOTHERBOARD",
                       "C_RAM", "C_COOLING_FAN", "C_FIRMWARE", "C_OS_SOFTWARE"],
    "P_AEROBOOK_AIR": ["C_BATTERY", "C_AC_ADAPTER", "C_CHARGING_PORT", "C_DISPLAY_PANEL", "C_DISPLAY_CABLE",
                       "C_KEYBOARD", "C_KEYBOARD_CABLE", "C_TOUCHPAD", "C_WIFI_CARD", "C_ANTENNA", "C_MOTHERBOARD",
                       "C_FIRMWARE", "C_OS_SOFTWARE"],
    "P_WORKMATE": ["C_BATTERY", "C_AC_ADAPTER", "C_CHARGING_PORT", "C_DISPLAY_PANEL", "C_DISPLAY_CABLE", "C_GPU",
                   "C_KEYBOARD", "C_KEYBOARD_CABLE", "C_TOUCHPAD", "C_WIFI_CARD", "C_ANTENNA", "C_MOTHERBOARD",
                   "C_RAM", "C_COOLING_FAN", "C_FIRMWARE", "C_OS_SOFTWARE"],
}

PROFILE_RECIPES = {
    "Symptom": [["HAS_CHECKLIST"], ["HAS_CHECKLIST", "CONTAINS"], ["HAS_CHECKLIST", "CONTAINS", "GOVERNED_BY"],
                ["HAS_CHECKLIST", "CONTAINS", "~DESCRIBES"]],
    "DiagnosticChecklist": [["CONTAINS"], ["CONTAINS", "HAS_OUTCOME", "LEADS_TO"], ["CONTAINS", "GOVERNED_BY"]],
    "DiagnosticAction": [["HAS_OUTCOME", "LEADS_TO"], ["~CONTAINS", "~HAS_CHECKLIST"]],
    "Component": [["~INDICATES", "HAS_CALL_DRIVER"]],
    "CallDriver": [["~HAS_CALL_DRIVER", "INDICATES"]],
    "DecisionRule": [["~GOVERNED_BY", "~CONTAINS"]],
}


# ------------------------------------------------------------------ assembly

def outcome_id(action_id: str, code: str) -> str:
    return f"O_{action_id[2:]}__{code}"


def _prop_defs(items) -> list[dict]:
    return [{"name": n, "type": t, "description": d} for n, t, d in items]


def build_definition() -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(id_, type_, name, definition="", synonyms=(), **properties):
        nodes.append({"id": id_, "type": type_, "name": name, "definition": definition,
                      "synonyms": list(synonyms), "properties": properties})

    def edge(source, type_, target, confidence=1.0, **properties):
        item = {"source": source, "type": type_, "target": target}
        if properties:
            item["properties"] = properties
        if confidence != 1.0:
            item["confidence"] = confidence
        edges.append(item)

    for id_, name, definition, synonyms, segment in PRODUCTS:
        node(id_, "Product", name, definition, synonyms, segment=segment)
        for component in PRODUCT_COMPONENTS[id_]:
            edge(id_, "HAS_COMPONENT", component)
    for id_, name, product, year, removable, wattage, usb_c in MODELS:
        node(id_, "DeviceModel", name, f"{name} laptop model.", [name.split(" (")[0]], release_year=year,
             removable_battery=removable, charger_wattage=wattage, usb_c_charging=usb_c)
        edge(product, "HAS_MODEL", id_)
    for id_, name, definition, synonyms, kind, replaceable, parent in COMPONENTS:
        node(id_, "Component", name, definition, synonyms, kind=kind, customer_replaceable=replaceable)
        if parent:
            edge(id_, "PART_OF", parent)
    for id_, name, definition, synonyms in CALL_DRIVERS:
        node(id_, "CallDriver", name, definition, synonyms)
    for id_, name, definition, synonyms, level, severity, parent, driver, checklist, components in SYMPTOMS:
        node(id_, "Symptom", name, definition, synonyms, level=level, severity=severity)
        if parent:
            edge(id_, "SUBTYPE_OF", parent)
        edge(id_, "HAS_CALL_DRIVER", driver)
        if checklist:
            edge(id_, "HAS_CHECKLIST", checklist)
        for component, likelihood in components.items():
            edge(id_, "INDICATES", component, confidence=0.9, likelihood=likelihood)

    outcomes = {a[0]: a[8] for a in ACTIONS}
    for id_, name, definition, synonyms, minutes, risk, remote, kind, action_outcomes in ACTIONS:
        node(id_, "DiagnosticAction", name, definition, synonyms, estimated_minutes=minutes, risk=risk,
             remote_capable=remote, action_kind=kind)
        for code, label, resolution in action_outcomes:
            oid = outcome_id(id_, code)
            node(oid, "ActionOutcome", f"{name}: {label}", f"Outcome of '{name}': {label.lower()}.",
                 [label] + OUTCOME_SYNONYMS.get(code, []), code=code, resolution=resolution)
            edge(id_, "HAS_OUTCOME", oid)

    membership: dict[str, list[str]] = {}
    for id_, name, definition, owner, steps in CHECKLISTS:
        node(id_, "DiagnosticChecklist", name, definition, [name.replace(" Checklist", " procedure")], owner=owner)
        for position, action in enumerate(steps, 1):
            edge(id_, "CONTAINS", action, step_order=position)
            membership.setdefault(action, []).append(id_)

    # Outcome -> next action. Default: the following step of the same checklist.
    order = {c[0]: c[4] for c in CHECKLISTS}
    for action_id, action_outcomes in outcomes.items():
        for code, _label, resolution in action_outcomes:
            if resolution != "continue":
                continue
            branches: dict[tuple[str, str], list[str]] = {}  # (extra condition, next action) -> checklists
            for checklist in membership.get(action_id, []):
                override = TRANSITIONS.get((checklist, action_id, code), TRANSITIONS.get(("*", action_id, code)))
                if override is None:
                    steps = order[checklist]
                    position = steps.index(action_id)
                    override = steps[position + 1] if position + 1 < len(steps) else "A_ESCALATE_L2"
                for extra, nxt in (override if isinstance(override, list) else [("", override)]):
                    branches.setdefault((extra, nxt), []).append(checklist)
            if not membership.get(action_id):  # dispositions reached only through transitions
                override = TRANSITIONS.get(("*", action_id, code))
                if override:
                    branches[("", override)] = []
            everywhere = set(membership.get(action_id, []))
            for (extra, nxt), checklists in sorted(branches.items()):
                parts = []
                if checklists and set(checklists) != everywhere:
                    parts.append(f"checklist_id in {sorted(checklists)!r}" if len(checklists) > 1
                                 else f"checklist_id == {checklists[0]!r}")
                if extra:
                    parts.append(extra)
                properties = {"condition": " and ".join(parts)} if parts else {}
                edge(outcome_id(action_id, code), "LEADS_TO", nxt, **properties)

    for id_, name, definition, expression, category, message, governed in RULES:
        node(id_, "DecisionRule", name, definition, [], expression=expression, category=category,
             failure_message=message)
        for action in governed:
            edge(action, "GOVERNED_BY", id_)
    for id_, title, summary, status, reviewed, described in ARTICLES:
        node(id_, "KnowledgeArticle", title, summary, [id_.replace("_", "-")], status=status,
             last_reviewed=reviewed, summary=summary, url=f"https://kb.example.invalid/{id_.lower()}")
        for action in described:
            edge(id_, "DESCRIBES", action)
    for newer, older in SUPERSEDES:
        edge(newer, "SUPERSEDES", older)

    object_types = [{"name": n, "description": d, "properties": _prop_defs(p)} for n, d, p in OBJECT_TYPES]
    object_types += [{"name": n, "description": d, "storage": "operational", "table": t, "key_column": k}
                     for n, d, t, k in OPERATIONAL_TYPES]
    relationship_types = [{"name": n, "description": d, "source_types": s, "target_types": t, "hierarchical": h,
                           "properties": _prop_defs(p)} for n, d, s, t, h, p in RELATIONSHIP_TYPES]
    relationship_types += [{"name": n, "description": d, "source_types": s, "target_types": t,
                            "storage": "operational", "table": table, "source_column": sc, "target_column": tc}
                           for n, d, s, t, table, sc, tc in OPERATIONAL_RELATIONSHIPS]
    return {
        "ontology": {
            "name": "laptop_support", "version": VERSION, "synthetic": True,
            "description": "Synthetic ontology for IT customer service and laptop troubleshooting.",
            "source": "ontolab.sample.laptop_support (synthetic)",
        },
        "object_types": object_types,
        "relationship_types": relationship_types,
        "profile_recipes": PROFILE_RECIPES,
        "nodes": nodes,
        "edges": edges,
    }


def write(path: str | Path | None = None) -> Path:
    path = Path(path) if path else Path(__file__).resolve().parents[2] / "data" / "ontology" / "laptop_support.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# SYNTHETIC DATA. Generated by `python -m ontolab.sample.laptop_support`; edit that script, not this file.\n"
              "# Invented products, procedures, rules and articles for demonstration only.\n")
    path.write_text(header + yaml.safe_dump(build_definition(), sort_keys=False, width=160, allow_unicode=True),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"wrote {write()}")
