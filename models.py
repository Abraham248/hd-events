from google.appengine.ext import db
from google.appengine.api import urlfetch, memcache, users, mail
from datetime import datetime, timedelta
from icalendar import Calendar, Event as CalendarEvent
from utils import human_username, local_today
import logging

ROOM_OPTIONS = (
    ('Cave', 15),
    ('Deck', 30),
    ('Savanna', 120),
    ('140b', 129),
    ('Cubby 1', 2),
    ('Cubby 2', 2),
    ('Cubby 3', 2),
    ('Upstairs Office', 2),
    ('Front Area', 20))
GUESTS_PER_STAFF = 25
PENDING_LIFETIME = 30 # days

def to_sentence(aList):
    sentence = ', '.join([e for e in aList if aList.index(e) != len(aList) -1])
    if len(aList) > 1: sentence = '%s and %s' % (sentence, aList[-1])
    return sentence

def to_name_list(aList):
    sentence = ', '.join([human_username(e) for e in aList if aList.index(e) != len(aList) -1])
    if len(aList) > 1: sentence = '%s and %s' % (sentence, human_username(aList[-1]))
    return sentence

class Event(db.Model):
    status = db.StringProperty(required=True, default='pending', choices=set(
        ['pending', 'understaffed', 'approved', 'canceled', 'onhold', 'expired', 'deleted']))
    member = db.UserProperty(auto_current_user_add=True)
    name = db.StringProperty(required=True)
    start_time = db.DateTimeProperty(required=True)
    end_time = db.DateTimeProperty()
    staff = db.ListProperty(users.User)
    rooms = db.StringListProperty() #choices=set(ROOM_OPTIONS)

    details = db.StringProperty(multiline=True)
    url = db.StringProperty()
    fee = db.StringProperty()
    notes = db.StringProperty(multiline=True)
    type = db.StringProperty(required=True)
    estimated_size = db.StringProperty(required=True)

    contact_name = db.StringProperty(required=True)
    contact_phone = db.StringProperty(required=True)

    expired = db.DateTimeProperty()
    created = db.DateTimeProperty(auto_now_add=True)
    updated = db.DateTimeProperty(auto_now=True)

    @classmethod
    def get_approved_list(cls):
        return cls.all() \
            .filter('start_time >', local_today()) \
            .filter('status IN', ['approved', 'canceled']) \
            .order('start_time')

    @classmethod
    def get_pending_list(cls):
        return cls.all() \
            .filter('start_time >', local_today()) \
            .filter('status IN', ['pending', 'understaffed', 'onhold', 'expired']) \
            .order('start_time')

    def stafflist(self):
        return to_name_list(self.staff)

    def roomlist(self):
        return to_sentence(self.rooms)

    def is_staffed(self):
        return len(self.staff) >= self.staff_needed()

    def staff_needed(self):
      if self.estimated_size.isdigit():
        return int(self.estimated_size) / GUESTS_PER_STAFF
      else:
        # invalid data; just return something reasonable
        return 2

    def is_approved(self):
        '''Has the events team approved the event?  Note: This does not
        necessarily imply that the event is in state 'approved'.'''
        return self.status in ('understaffed', 'approved', 'canceled')

    def is_approved(self):
        '''Has the events team approved the event?  Note: This does not
        necessarily imply that the event is in state 'approved'.'''
        return self.status in ('understaffed', 'approved', 'cancelled')

    def is_canceled(self):
        return self.status == 'canceled'

    def is_deleted(self):
        return self.status == 'deleted'

    def is_past(self):
        return self.end_time < local_today()

    def start_date(self):
        return self.start_time.date()

    def approve(self):
        user = users.get_current_user()
        if self.is_staffed():
            self.expired = None
            self.status = 'approved'
            logging.info('%s approved %s' % (user.nickname, self.name))
        else:
            self.status = 'understaffed'
            logging.info('%s approved %s but it is still understaffed' % (user.nickname, self.name))
        self.put()

    def cancel(self):
        user = users.get_current_user()
        self.status = 'canceled'
        self.put()
        logging.info('%s canceled %s' % (user.nickname, self.name))

    def delete(self):
        user = users.get_current_user()
        self.status = 'deleted'
        self.put()
        logging.info('%s deleted %s' % (user.nickname, self.name))

    def undelete(self):
        user = users.get_current_user()
        self.status = 'pending'
        self.put()
        logging.info('%s undeleted %s' % (user.nickname, self.name))

    def delete(self):
        user = users.get_current_user()
        self.status = 'deleted'
        self.put()
        logging.info('%s deleted %s' % (user.nickname, self.name))

    def undelete(self):
        user = users.get_current_user()
        self.status = 'pending'
        self.put()
        logging.info('%s undeleted %s' % (user.nickname, self.name))

    def expire(self):
        user = users.get_current_user()
        self.expired = datetime.now()
        self.status = 'expired'
        self.put()
        logging.info('%s expired %s' % (user.nickname, self.name))

    def add_staff(self, user):
        self.staff.append(user)
        if self.is_staffed() and self.status == 'understaffed':
            self.status = 'approved'
        self.put()
        logging.info('%s staffed %s' % (user.nickname, self.name))

    def remove_staff(self, user):
        self.staff.remove(user)
        if not self.is_staffed() and self.status == 'approved':
            self.status = 'understaffed'
        self.put()
        logging.info('%s staffed %s' % (user.nickname, self.name))

    def to_ical(self):
        event = CalendarEvent()
        event.add('summary', self.name if self.status == 'approved' else self.name + ' (%s)' % self.status.upper())
        event.add('dtstart', self.start_time.replace(tzinfo=timezone('US/Pacific')))
        event.add('dtend', self.end_time.replace(tzinfo=timezone('US/Pacific')))
        return event

class Feedback(db.Model):
    user = db.UserProperty(auto_current_user_add=True)
    event = db.ReferenceProperty(Event)
    rating = db.IntegerProperty()
    comment = db.StringProperty(multiline=True)
    created = db.DateTimeProperty(auto_now_add=True)
