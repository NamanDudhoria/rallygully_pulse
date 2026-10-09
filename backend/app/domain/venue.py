import datetime
from enum import Enum
from uuid import uuid4


class WeeklySchedule:
    def __init__(self):
        self.schedule = {day: [] for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']}
    def add_time_slot(self, day, start_time, end_time):
        if day not in self.schedule:
            raise ValueError("Invalid day. Must be a valid day of the week.")
        if not isinstance(start_time, datetime.time) or not isinstance(end_time, datetime.time):
            raise ValueError("Start time and end time must be datetime.time objects.")
        if start_time >= end_time:
            raise ValueError("Start time must be before end time.")
        if (start_time.minute not in (0, 30) or end_time.minute not in (0, 30)
                or start_time.second != 0 or end_time.second != 0
                or start_time.microsecond != 0 or end_time.microsecond != 0):
            raise ValueError("Start and end times must be on the hour or half-hour.")
        self.schedule[day].append((start_time, end_time))

    def generate_slots(self):
        slots = {}
        for day, time_ranges in self.schedule.items():
            slots[day] = []
            for start_time, end_time in time_ranges:
                current_time = start_time
                while current_time < end_time:
                    next_time = (datetime.datetime.combine(datetime.date.today(), current_time) + datetime.timedelta(minutes=30)).time()
                    if next_time > end_time:
                        break
                    slots[day].append((current_time, next_time))
                    current_time = next_time
        return slots

def valid_status(status):
            if status not in ['Active', 'Inactive']:
                raise ValueError("Invalid status. Must be 'Active' or 'Inactive'")
            return status
Sports = ["Badminton", "Pickleball" , "Paddle" , "6V6 Turf", "9V9 Turf", "Box Cricket"]


class InventoryAllocation(str, Enum):
    DIRECT = "Direct"
    DISTRICT = "District"
    COMMUNITY_GAMES = "Community Games"
    ACADEMY = "Academy"
    CORPORATE_PRIVATE_EVENTS = "Corporate/Private Events"
    OPERATIONAL_BLOCK = "Operational Blocks"


class PricingRule:
    """Versioned per-slot pricing configuration; amounts remain provisional."""

    def __init__(self, version, price_per_slot):
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Pricing rule version must be a non-empty string.")
        if not isinstance(price_per_slot, (int, float)) or price_per_slot < 0:
            raise ValueError("Price per slot must be a non-negative number.")
        self.version = version.strip()
        self.price_per_slot = price_per_slot


class Facility:
    def __init__(self, name, sport, status, price_per_slot, venue):
        self.name = name
        if sport not in Sports:
            raise ValueError(f"Invalid sport. Must be one of {Sports}")
        self.sport = sport
        self.price_per_slot = price_per_slot
        self.id = uuid4()
        self.status = valid_status(status)
        self.bookings = []
        initial_pricing_rule = PricingRule("v1", price_per_slot)
        self.pricing_rules = {initial_pricing_rule.version: initial_pricing_rule}
        self.active_pricing_version = initial_pricing_rule.version
        # Operational and maintenance blocks affect availability, but are not bookings
        # and therefore never contribute to booking revenue.
        self.operational_blocks = []
        if not isinstance(venue, Venue):
            raise ValueError("Invalid venue. Must be an instance of the Venue class.")
        self.venue = venue

    def add_pricing_rule(self, price_per_slot, version=None, activate=True):
        """Add a price configuration without changing prices on existing bookings."""
        if version is None:
            version = f"v{len(self.pricing_rules) + 1}"
        rule = PricingRule(version, price_per_slot)
        if rule.version in self.pricing_rules:
            raise ValueError("A pricing rule with this version already exists.")
        self.pricing_rules[rule.version] = rule
        if activate:
            self.active_pricing_version = rule.version
            self.price_per_slot = rule.price_per_slot
        return rule

    def update_status(self, new_status):
        self.status = valid_status(new_status)

    def mass_discount(self, discount_percentage, no_of_slots):
        total_price = self.price_per_slot * no_of_slots
        discount_amount = total_price * (discount_percentage / 100)
        return total_price - discount_amount

    def generic_discount(self, discount_percentage):
        discount_amount = self.price_per_slot * (discount_percentage / 100)
        return self.price_per_slot - discount_amount

    def add_operational_block(self, booking_date, start_time, end_time, reason):
        """Block a time for maintenance or an operational issue (not a revenue booking)."""
        if not isinstance(booking_date, datetime.date) or isinstance(booking_date, datetime.datetime):
            raise ValueError("Block date must be a datetime.date object.")
        if not isinstance(start_time, datetime.time) or not isinstance(end_time, datetime.time):
            raise ValueError("Block times must be datetime.time objects.")
        if start_time >= end_time:
            raise ValueError("Block start time must be before end time.")
        if any(time.minute not in (0, 30) or time.second != 0 or time.microsecond != 0
               for time in (start_time, end_time)):
            raise ValueError("Block times must be on the hour or half-hour.")
        if reason not in ("OperationalIssue", "Maintenance"):
            raise ValueError("Reason must be 'OperationalIssue' or 'Maintenance'.")
        block = {
            "booking_date": booking_date,
            "start_time": start_time,
            "end_time": end_time,
            "reason": reason,
            "allocation_type": InventoryAllocation.OPERATIONAL_BLOCK,
        }
        self.operational_blocks.append(block)
        return block

    def total_revenue(self):
        """Revenue from confirmed bookings only; operational blocks are excluded."""
        return sum(booking.total_price for booking in self.bookings
                   if booking.status == Booking.CONFIRMED)

    def revenue_by_channel(self):
        """Attribute confirmed booking revenue to its channel (for example, direct)."""
        revenue = {}
        for booking in self.bookings:
            if booking.status == Booking.CONFIRMED:
                revenue[booking.channel] = revenue.get(booking.channel, 0) + booking.total_price
        return revenue

    def book(self, booking_date, start_time, end_time, channel="direct",
             allocation_type=InventoryAllocation.DIRECT, pricing_version=None):
        """Create bookings from any channel through the same availability checks."""
        return Booking(self, booking_date, start_time, end_time, channel=channel,
                       allocation_type=allocation_type, pricing_version=pricing_version)

    def current_booking(self, at=None):
        """Return this facility's confirmed booking active at ``at``."""
        at = at or datetime.datetime.now()
        if not isinstance(at, datetime.datetime):
            raise ValueError("Occupancy time must be a datetime.datetime object.")
        local_time = at.timetz().replace(tzinfo=None)
        return next((booking for booking in self.bookings
                     if booking.status == Booking.CONFIRMED
                     and booking.booking_date == at.date()
                     and booking.start_time <= local_time < booking.end_time), None)

    def operational_status(self, at=None):
        if self.status != "Active" or self.venue.status != "Active":
            return "Unavailable"
        at = at or datetime.datetime.now()
        local_time = at.timetz().replace(tzinfo=None)
        block = next((block for block in self.operational_blocks
                      if block["booking_date"] == at.date()
                      and block["start_time"] <= local_time < block["end_time"]), None)
        if block:
            return block["reason"]
        return "Occupied" if self.current_booking(at) else "Available"

    def cancel_booking(self, booking_id):
        for booking in self.bookings:
            if booking.id == booking_id:
                booking.cancel()
                return booking
        raise ValueError("Booking not found for this facility.")


class Booking:
    CONFIRMED = "Confirmed"
    CANCELLED = "Cancelled"

    def __init__(self, facility, booking_date, start_time, end_time, channel="direct",
                 allocation_type=InventoryAllocation.DIRECT, pricing_version=None):
        if not isinstance(facility, Facility):
            raise ValueError("Invalid facility. Must be an instance of the Facility class.")
        if not isinstance(booking_date, datetime.date) or isinstance(booking_date, datetime.datetime):
            raise ValueError("Booking date must be a datetime.date object.")
        if not isinstance(start_time, datetime.time) or not isinstance(end_time, datetime.time):
            raise ValueError("Start time and end time must be datetime.time objects.")
        if start_time >= end_time:
            raise ValueError("Start time must be before end time.")
        if any(time.minute not in (0, 30) or time.second != 0 or time.microsecond != 0
               for time in (start_time, end_time)):
            raise ValueError("Booking times must be on the hour or half-hour.")
        if facility.status != "Active" or facility.venue.status != "Active":
            raise ValueError("Bookings require an active facility and venue.")
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("Booking channel must be a non-empty string.")
        try:
            allocation_type = InventoryAllocation(allocation_type)
        except (TypeError, ValueError):
            raise ValueError("Invalid inventory allocation type.") from None
        if allocation_type == InventoryAllocation.OPERATIONAL_BLOCK:
            raise ValueError("Use add_operational_block() for operational inventory blocks.")

        day = booking_date.strftime("%A")
        available_slots = set(facility.venue.weekly_schedule.generate_slots().get(day, []))
        requested_slots = []
        current_time = start_time
        while current_time < end_time:
            next_time = (datetime.datetime.combine(booking_date, current_time)
                         + datetime.timedelta(minutes=30)).time()
            requested_slots.append((current_time, next_time))
            current_time = next_time
        if not requested_slots or any(slot not in available_slots for slot in requested_slots):
            raise ValueError("Requested time is outside the venue's available schedule.")

        if any(block["booking_date"] == booking_date
               and start_time < block["end_time"] and end_time > block["start_time"]
               for block in facility.operational_blocks):
            raise ValueError("Requested time is blocked for an operational issue or maintenance.")

        for existing in facility.bookings:
            if (existing.status == self.CONFIRMED and existing.booking_date == booking_date
                    and start_time < existing.end_time and end_time > existing.start_time):
                raise ValueError("This facility is already booked for the requested time.")

        self.id = uuid4()
        self.facility = facility
        self.booking_date = booking_date
        self.start_time = start_time
        self.end_time = end_time
        self.channel = channel.strip()
        self.allocation_type = allocation_type
        self.status = self.CONFIRMED
        self.slot_count = len(requested_slots)
        pricing_version = pricing_version or facility.active_pricing_version
        if pricing_version not in facility.pricing_rules:
            raise ValueError("Unknown pricing rule version for this facility.")
        self.pricing_version = pricing_version
        self.pricing_status = "Provisional"
        self.total_price = (facility.pricing_rules[pricing_version].price_per_slot
                            * self.slot_count)
        facility.bookings.append(self)

    def cancel(self):
        if self.status != self.CONFIRMED:
            raise ValueError("Only confirmed bookings can be cancelled.")
        self.status = self.CANCELLED


class Venue:
    def __init__(self, name, address, status,):
        self.name = name
        self.address = address
        self.id = uuid4()
        self.status = valid_status(status)
        self.weekly_schedule = WeeklySchedule()
        self.facilities = []

    def update_status(self, new_status):
        self.status = valid_status(new_status)

    def add_facility(self, facility):
        if not isinstance(facility, Facility):
            raise ValueError("Invalid facility. Must be an instance of the Facility class.")
        if facility.venue != self:
            raise ValueError("Facility's venue does not match this venue.")
        self.facilities.append(facility)

    def operational_occupancy(self, at=None):
        """Summarize current occupancy for the venue's active facilities."""
        at = at or datetime.datetime.now()
        if not isinstance(at, datetime.datetime):
            raise ValueError("Occupancy time must be a datetime.datetime object.")
        active = [facility for facility in self.facilities
                  if self.status == "Active" and facility.status == "Active"]
        occupied = [facility for facility in active
                    if facility.current_booking(at) is not None]
        return {
            "occupied": len(occupied),
            "active": len(active),
            "occupancy_rate": len(occupied) / len(active) if active else 0.0,
            "facilities": {facility.id: facility.operational_status(at)
                           for facility in self.facilities},
        }



