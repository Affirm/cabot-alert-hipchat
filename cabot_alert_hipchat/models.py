import json
from django.db import models
from urlparse import urljoin
from cabot.cabotapp.alert import AlertPlugin, AlertPluginUserData

from os import environ as env

from django.conf import settings
from django.template import Context, Template

import requests

hipchat_template = "Service {{ service.name|safe }} {% if service.overall_status == service.PASSING_STATUS %}is back to normal{% else %}reporting {{ service.overall_status }} status{% endif %}: {{ scheme }}://{{ host }}{% url 'service' pk=service.id %}. {% if service.overall_status != service.PASSING_STATUS %}Checks failing: {% for check in service.all_failing_checks %}{% if check.check_category == 'Jenkins check' %}{% if check.last_result.error %} {{ check.name }} ({{ check.last_result.error|safe }}) {{jenkins_api}}job/{{ check.name }}/{{ check.last_result.job_number }}/console{% else %} {{ check.name }} {{jenkins_api}}/job/{{ check.name }}/{{check.last_result.job_number}}/console {% endif %}{% else %} {{ check.name }} {% if check.last_result.error %} ({{ check.last_result.error|safe }}){% endif %}{% endif %}{% endfor %}{% endif %}{% if alert %}{% for alias in users %} @{{ alias }}{% endfor %}{% endif %}"

# This provides the hipchat alias for each user. Each object corresponds to a User
class HipchatAlert(AlertPlugin):
    name = "Hipchat"
    author = "Jonathan Balls"

    def _send_hipchat_alert(self, message, service, color='green', sender='Cabot'):
        """
        Send an alert with the service status, then find the failing checks for a service
        and post their images to Hipchat
        :param message: the message to post
        :param service: the Service we're alerting for
        :param color: the color the message will appear (red for error, green for back to normal)
        :param sender: who to send the message as
        :return: None
        """
        if service.hipchat_instance is not None:
            url = service.hipchat_instance.server_url
            api_key = service.hipchat_instance.api_key
        else:
            # Backwards compatibility
            # HIPCHAT_URL has /api/v1 built in, but we need to use v2 for images
            url = env.get('HIPCHAT_URL').split('v1')[0]
            api_key = env.get('HIPCHAT_API_V2_KEY')

        if service.hipchat_room_id is not None:
            hipchat_room = service.hipchat_room_id
        else:
            hipchat_room = env.get('HIPCHAT_ALERT_ROOM')

        url = urljoin(url, 'v2/room/{}/'.format(hipchat_room))

        status_headers = image_headers = {'Authorization': 'Bearer {}'.format(api_key)}

        # Send the status message
        status_url = urljoin(url, 'notification')
        status_headers['Content-type'] = 'application/json'
        data = {
            'from': sender[:60],
            'color': color,
            'message': message,
            'notify': True,
            'message_format': 'text'
        }
        requests.post(status_url, headers=status_headers, data=json.dumps(data))

        failing_checks = service.all_failing_checks()
        # Send the image messages
        if failing_checks == []:
            return

        image_url = urljoin(url, 'share/file')
        image_headers['Content-type'] = 'multipart/related; boundary=boundary123456'

        for check in failing_checks:
            image = check.get_status_image()
            if image is not None:
                # See https://www.hipchat.com/docs/apiv2/method/share_file_with_room
                # and https://gist.github.com/bdclark/0cbadce5816b6ab10eb2
                data = '--boundary123456\n'\
                       'Content-Type: image/png\n'\
                       'Content-Disposition: attachment; name="file"; filename="{}.png"\n'\
                       '{}\n'\
                       '--boundary123456--'.format(check.name, image)

                requests.post(image_url, headers=image_headers, data=data)

    def send_alert(self, service, users, duty_officers):
        alert = True
        users = list(users) + list(duty_officers)

        hipchat_aliases = [u.hipchat_alias for u in HipchatAlertUserData.objects.filter(user__user__in=users)]

        if service.overall_status == service.WARNING_STATUS:
            alert = False  # Don't alert at all for WARNING
        if service.overall_status == service.ERROR_STATUS:
            if service.old_overall_status in (service.ERROR_STATUS, service.ERROR_STATUS):
                alert = False  # Don't alert repeatedly for ERROR
        if service.overall_status == service.PASSING_STATUS:
            color = 'green'
            if service.old_overall_status == service.WARNING_STATUS:
                alert = False  # Don't alert for recovery from WARNING status
        else:
            color = 'red'

        jenkins_api = urljoin(settings.JENKINS_API, '/')
        c = Context({
            'service': service,
            'users': hipchat_aliases,
            'host': settings.WWW_HTTP_HOST,
            'scheme': settings.WWW_SCHEME,
            'alert': alert,
            'jenkins_api': jenkins_api,
        })

        message = Template(hipchat_template).render(c)
        self._send_hipchat_alert(message,
                                 service,
                                 color=color,
                                 sender='Cabot/%s' % service.name)


class HipchatAlertUserData(AlertPluginUserData):
    name = "Hipchat Plugin"
    hipchat_alias = models.CharField(max_length=50, blank=True)

