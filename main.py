from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util, template
from google.appengine.api import urlfetch, memcache, users, mail

from django.utils import simplejson
from django.template.defaultfilters import slugify
from icalendar import Calendar
import logging, urllib

from datetime import datetime, timedelta, time, date
from models import Event, Feedback, ROOM_OPTIONS, GUESTS_PER_STAFF, PENDING_LIFETIME, FROM_ADDRESS

# Hacker Dojo Domain API helper with caching
def dojo(path):
    base_url = 'http://hackerdojo-domain.appspot.com'
    cache_ttl = 3600
    resp = memcache.get(path)
    if not resp:
        resp = urlfetch.fetch(base_url + path, deadline=10)
        try:
            resp = simplejson.loads(resp.content)
        except Exception, e:
            resp = []
            cache_ttl = 10
        memcache.set(path, resp, cache_ttl)
    return resp

def username(user):
    return user.nickname().split('@')[0] if user else None

def human_username(user):
    if user:
        nick = user.nickname().split('@')[0]
        first = nick.split('.')[0]
        last = nick.split('.')[1]
        return first + ' ' + last
    else:
        return None

def notify_owner_confirmation(event):
    mail.send_mail(sender=FROM_ADDRESS, to=event.member.email(),
        subject="Event application submitted",
        body="""This is a confirmation that your event:\n\n%s\n\n
has been submitted for approval. If staff is needed for your event, they
will be notified of your request. You will be notified as soon as it's
approved and on the calendar.""" % event.name)

def notify_staff_needed(event):
    pass

def notify_new_event(event):
    pass

def notify_owner_approved(event):
    pass

def notify_owner_expiring(event):
    pass

def notify_owner_expired(event):
    pass

def set_cookie(headers, name, value):
    headers.add_header('Set-Cookie', '%s=%s;' % (name, simplejson.dumps(value)))

class ExpireCron(webapp.RequestHandler):    
    def post(self):
        # Expire events marked to expire today
        today = datetime.combine(datetime.today(), time())
        events = Event.all() \
            .filter('status IN', ['pending', 'understaffed']) \
            .filter('expired >=', today) \
            .filter('expired <', today + timedelta(days=1))
        for event in events:
            event.expire()
            notify_owner_expired(event)

class ExpireReminderCron(webapp.RequestHandler):
    def post(self):
        # Find events expiring in 10 days to warn owner
        ten_days = datetime.combine(datetime.today(), time()) + timedelta(days=10)
        events = Event.all() \
            .filter('status IN', ['pending', 'understaffed']) \
            .filter('expired >=', ten_days) \
            .filter('expired <', ten_days + timedelta(days=1))
        for event in events:
            notify_owner_expiring(event)

class EventsHandler(webapp.RequestHandler):
    def get(self, format):
        if format == 'ics':
            events = Event.all().filter('status IN', ['approved', 'canceled']).order('start_time')
            cal = Calendar()
            for event in events:
                cal.add_component(event.to_ical())
            self.response.headers['content-type'] = 'text/calendar'
            self.response.out.write(cal.as_string())

class EventHandler(webapp.RequestHandler):
    def get(self, id):
        event = Event.get_by_id(int(id))
        user = users.get_current_user()
        if user:
            is_admin = username(user) in dojo('/groups/events')
            is_staff = username(user) in dojo('/groups/staff')
            can_approve = (event.status in ['pending'] and is_admin)
            can_staff = (event.status in ['pending', 'understaffed', 'approved'] and is_staff and not user in event.staff)
            logout_url = users.create_logout_url('/')
            feedbacks = event.feedback_set
        else:
            login_url = users.create_login_url('/')
        self.response.out.write(template.render('templates/event.html', locals()))
    
    def post(self, id):
        event = Event.get_by_id(int(id))
        user = users.get_current_user()
        is_admin = username(user) in dojo('/groups/events')
        is_staff = username(user) in dojo('/groups/staff')
        state = self.request.get('state')
        if state:
            if state.lower() == 'approve' and is_admin:
                event.approve()
            if state.lower() == 'staff' and is_staff:
                event.add_staff(user)
            if state.lower() == 'cancel' and is_admin:
                event.cancel()
            if state.lower() == 'expire' and is_admin:
                event.expire()
            
            if event.status == 'approved':
                notify_owner_approved(event)
        self.redirect('/event/%s-%s' % (event.key().id(), slugify(event.name)))

class ApprovedHandler(webapp.RequestHandler):
    def get(self):
        user = users.get_current_user()
        if user:
            logout_url = users.create_logout_url('/')
        else:
            login_url = users.create_login_url('/')
        today = datetime.today()
        events = Event.get_approved_list()
        tomorrow = today + timedelta(days=1)
        self.response.out.write(template.render('templates/approved.html', locals()))

class MyEventsHandler(webapp.RequestHandler):
    @util.login_required
    def get(self):
        user = users.get_current_user()
        if user:
            logout_url = users.create_logout_url('/')
        else:
            login_url = users.create_login_url('/')
        events = Event.all().filter('member = ', user).order('start_time')
        today = datetime.today()
        tomorrow = today + timedelta(days=1)
        is_admin = username(user) in dojo('/groups/events')
        self.response.out.write(template.render('templates/myevents.html', locals()))

class PastHandler(webapp.RequestHandler):
    def get(self):
        user = users.get_current_user()
        if user:
            logout_url = users.create_logout_url('/')
        else:
            login_url = users.create_login_url('/')
        today = datetime.today()
        events = Event.all().filter('start_time < ', today).order('-start_time')
        is_admin = username(user) in dojo('/groups/events')
        self.response.out.write(template.render('templates/past.html', locals()))

class PendingHandler(webapp.RequestHandler):
    def get(self):
        user = users.get_current_user()
        if user:
            logout_url = users.create_logout_url('/')
        else:
            login_url = users.create_login_url('/')
        events = Event.get_pending_list()
        today = datetime.today()
        tomorrow = today + timedelta(days=1)
        is_admin = username(user) in dojo('/groups/events')
        self.response.out.write(template.render('templates/pending.html', locals()))

class FeedbackHandler(webapp.RequestHandler):
    @util.login_required
    def get(self, event_id):
        user = users.get_current_user()
        event = Event.get_by_id(int(event_id))
        if user:
            logout_url = users.create_logout_url('/')
        else:
            login_url = users.create_login_url('/')
        self.response.out.write(template.render('templates/feedback.html', locals()))
    
    def post(self, event_id):
        user = users.get_current_user()
        event = Event.get_by_id(int(event_id))
        feedback = Feedback(
            event = event,
            rating = int(self.request.get('rating')),
            comment = self.request.get('comment'))
        feedback.put()
        self.redirect('/event/%s-%s' % (event.key().id(), slugify(event.name)))


class NewHandler(webapp.RequestHandler):
    @util.login_required
    def get(self):
        user = users.get_current_user()
        if user:
            logout_url = users.create_logout_url('/')
        else:
            login_url = users.create_login_url('/')
        rooms = ROOM_OPTIONS
        self.response.out.write(template.render('templates/new.html', locals()))
    
    def post(self):
        user = users.get_current_user()
        try:
            start_time = datetime.strptime("%s %s:%s %s" % (
                self.request.get('date'),
                self.request.get('start_time_hour'),
                self.request.get('start_time_minute'),
                self.request.get('start_time_ampm')), "%d/%m/%Y %I:%M %p")
            end_time = datetime.strptime("%s %s:%s %s" % (
                self.request.get('date'),
                self.request.get('end_time_hour'),
                self.request.get('end_time_minute'),
                self.request.get('end_time_ampm')), "%d/%m/%Y %I:%M %p")
            if (end_time-start_time).days < 0:
                raise ValueError("End time must be after start time")
            else:
                event = Event(
                    name = self.request.get('name'),
                    start_time = start_time,
                    end_time = end_time,
                    type = self.request.get('type'),
                    estimated_size = self.request.get('estimated_size'),
                    contact_name = self.request.get('contact_name'),
                    contact_phone = self.request.get('contact_phone'),
                    details = self.request.get('details'),
                    url = self.request.get('url'),
                    fee = self.request.get('fee'),
                    notes = self.request.get('notes'),
                    rooms = self.request.get_all('rooms'),
                    expired = datetime.today() + timedelta(days=PENDING_LIFETIME), # Set expected expiration date
                    )
                event.put()
                notify_owner_confirmation(event)
                if not event.is_staffed():
                    notify_staff_needed(event)
                notify_new_event(event)
                self.redirect('/event/%s-%s' % (event.key().id(), slugify(event.name)))
        except Exception:
            set_cookie(self.response.headers, 'formvalues', dict(self.request.POST))
            self.redirect('/new')

def main():
    application = webapp.WSGIApplication([
        ('/', ApprovedHandler),
        ('/events\.(.+)', EventsHandler),
        ('/past', PastHandler),
        ('/pending', PendingHandler),
        ('/myevents', MyEventsHandler),
        ('/new', NewHandler),
        ('/event/(\d+).*', EventHandler),
        ('/event/feedback/(\d+).*', FeedbackHandler),
        ('/cron/expire', ExpireCron),
        ('/cron/expiring', ExpireReminderCron),  ],debug=True)
    util.run_wsgi_app(application)

if __name__ == '__main__':
    main()
