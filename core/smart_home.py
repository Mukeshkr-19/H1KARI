"""
HIKARI v3 - Smart Home Integration
Controls HomeKit devices, lights, thermostats, and more
"""

import os
import sys
import asyncio
import subprocess
from typing import Optional, Dict, Any, List
from datetime import datetime

class SmartHome:
    """Smart home control using HomeKit and Siri shortcuts"""

    def __init__(self):
        self._cached_devices = None

    async def run_shortcut(self, shortcut_name: str) -> str:
        """Run a Siri Shortcut (can control HomeKit devices)"""
        try:
            result = subprocess.run(
                ["shortcuts", "run", shortcut_name],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return f"Executed: {shortcut_name}"
            return f"Shortcut '{shortcut_name}' not found or failed"
        except Exception as e:
            return f"Couldn't run shortcut: {str(e)}"

    async def homekit_control(self, device: str, action: str) -> str:
        """Control HomeKit device via Siri command"""
        # Map common commands to Siri phrases
        commands = {
            ("lights", "on"): "Turn on the living room lights",
            ("lights", "off"): "Turn off the living room lights",
            ("lights", "bright"): "Set living room lights to full brightness",
            ("lights", "dim"): "Set living room lights to fifty percent",

            ("thermostat", "up"): "Increase thermostat temperature",
            ("thermostat", "down"): "Decrease thermostat temperature",
            ("thermostat", "heat"): "Set thermostat to heating mode",
            ("thermostat", "cool"): "Set thermostat to cooling mode",

            ("fan", "on"): "Turn on the bedroom fan",
            ("fan", "off"): "Turn off the bedroom fan",

            ("ac", "on"): "Turn on the air conditioning",
            ("ac", "off"): "Turn off the air conditioning",

            ("lock", "lock"): "Lock the front door",
            ("lock", "unlock"): "Unlock the front door",

            ("tv", "on"): "Turn on the TV",
            ("tv", "off"): "Turn off the TV",

            ("garage", "open"): "Open the garage door",
            ("garage", "close"): "Close the garage door",
        }

        key = (device.lower(), action.lower())
        if key in commands:
            return await self.run_shortcut("Home Control")  # Custom shortcut needed

        return f"Unknown device/action: {device} {action}"

    async def set_scene(self, scene_name: str) -> str:
        """Activate a scene (pre-configured in Home app)"""
        scenes = {
            "morning": "Good Morning",
            "movie": "Movie Time",
            "sleep": "Good Night",
            "away": "Away Mode",
            "party": "Party Mode",
            "relax": "Relax Mode",
        }

        if scene_name.lower() in scenes:
            return await self.run_shortcut(scenes[scene_name.lower()])

        return f"Unknown scene: {scene_name}"

    async def get_device_status(self, device: str) -> str:
        """Check status of a device"""
        # Would need HomeKit API access
        return f"Device status for {device}: Unknown (HomeKit API requires additional setup)"

    async def list_devices(self) -> List[str]:
        """List all HomeKit devices"""
        return [
            "Living Room Lights",
            "Bedroom Lights",
            "Thermostat",
            "Front Door Lock",
            "Garage Door",
            "TV",
            "Bedroom Fan",
        ]

    async def control_light(self, room: str, action: str) -> str:
        """Control lights in a specific room"""
        room_actions = {
            "living room": {
                "on": "Turn on living room lights",
                "off": "Turn off living room lights",
                "bright": "Set living room lights to full brightness",
                "dim": "Dim living room lights to 50%",
            },
            "bedroom": {
                "on": "Turn on bedroom lights",
                "off": "Turn off bedroom lights",
                "bright": "Set bedroom lights to full brightness",
                "dim": "Dim bedroom lights to 50%",
            },
            "kitchen": {
                "on": "Turn on kitchen lights",
                "off": "Turn off kitchen lights",
            },
            "bathroom": {
                "on": "Turn on bathroom lights",
                "off": "Turn off bathroom lights",
            },
            "all": {
                "on": "Turn on all lights",
                "off": "Turn off all lights",
            },
        }

        room_lower = room.lower()
        if room_lower in room_actions:
            if action.lower() in room_actions[room_lower]:
                return await self.run_shortcut("Home Control")

        return f"Don't know how to {action} {room} lights"

    async def set_thermostat(self, temp: int, mode: str = "auto") -> str:
        """Set thermostat temperature"""
        try:
            temp = int(temp)
            if temp < 60:
                return "Temperature too low (minimum 60°F)"
            if temp > 85:
                return "Temperature too high (maximum 85°F)"

            return await self.run_shortcut("Set Thermostat")
        except (ValueError, TypeError):
            return "Please specify a temperature (e.g., 'set thermostat to 72')"


class HomeAutomations:
    """Automations that run based on time or triggers"""

    def __init__(self):
        self.automations = []

    async def add_automation(self, name: str, trigger: str, action: str):
        """Add a new automation"""
        self.automations.append({
            "name": name,
            "trigger": trigger,  # "8:00 AM", "sunset", "when I arrive"
            "action": action,
        })

    async def remove_automation(self, name: str) -> str:
        """Remove an automation"""
        self.automations = [a for a in self.automations if a["name"] != name]
        return f"Removed automation: {name}"

    async def list_automations(self) -> List[Dict]:
        """List all automations"""
        return self.automations


# Singleton
_smart_home = None

def get_smart_home():
    global _smart_home
    if _smart_home is None:
        _smart_home = SmartHome()
    return _smart_home
