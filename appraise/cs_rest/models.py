﻿# -*- coding: utf-8 -*-
"""
Project: Appraise evaluation system
 Author: Christian Federmann <cfedermann@gmail.com>
"""
import logging
import uuid

from datetime import datetime
from xml.etree.ElementTree import fromstring, ParseError, tostring

from django.dispatch import receiver

from django.contrib.auth.models import User, Group
from django.core.urlresolvers import reverse
from django.core.validators import RegexValidator
from django.db import models
from django.template import Context
from django.template.loader import get_template

from appraise.cs_rest.validators import validate_hit_xml, validate_segment_xml
from appraise.settings import LOG_LEVEL, LOG_HANDLER
from appraise.utils import datetime_to_seconds, AnnotationTask

# Setup logging support.
logging.basicConfig(level=LOG_LEVEL)
LOGGER = logging.getLogger('appraise.cs_rest.models')
LOGGER.addHandler(LOG_HANDLER)

# How many users can annotate a given HIT
MAX_USERS_PER_HIT = 1

LANGUAGE_PAIR_CHOICES = (
  # News task languages
  ('ina2ces', 'DA → Czech'),
)

ISO639_3_TO_NAME_MAPPING = {
  'ina': 'DA',
  'ces': 'Czech', 'cze': 'Czech',
}

GROUP_HIT_REQUIREMENTS = {
  # volunteers
  'CUNI': 0,
}


# pylint: disable-msg=E1101
class HIT(models.Model):
    """
    HIT object model for cs_rest ranking evaluation.

    Each HIT contains 3 RankingTask instances for 3 consecutive sentences.

    """
    hit_id = models.CharField(
      max_length=8,
      db_index=True,
      unique=True,
      editable=False,
      help_text="Unique identifier for this HIT instance.",
      verbose_name="HIT identifier"
    )

    block_id = models.IntegerField(
      db_index=True,
      help_text="Block ID for this HIT instance.",
      verbose_name="HIT block identifier"
    )

    hit_xml = models.TextField(
      help_text="XML source for this HIT instance.",
      validators=[validate_hit_xml],
      verbose_name="HIT source XML"
    )

    language_pair = models.CharField(
      max_length=7,
      choices=LANGUAGE_PAIR_CHOICES,
      db_index=True,
      help_text="Language pair choice for this HIT instance.",
      verbose_name="Language pair"
    )

    # This is derived from hit_xml and NOT stored in the database.
    hit_attributes = {}

    users = models.ManyToManyField(
      User,
      blank=True,
      db_index=True,
      null=True,
      help_text="Users who work on this HIT instance."
    )

    active = models.BooleanField(
      db_index=True,
      default=True,
      help_text="Indicates that this HIT instance is still in use.",
      verbose_name="Active?"
    )

    mturk_only = models.BooleanField(
      db_index=True,
      default=False,
      help_text="Indicates that this HIT instance is ONLY usable via MTurk.",
      verbose_name="MTurk only?"
    )

    completed = models.BooleanField(
      db_index=True,
      default=False,
      help_text="Indicates that this HIT instance is completed.",
      verbose_name="Completed?"
    )

    assigned = models.DateTimeField(blank=True, null=True, editable=False)

    finished = models.DateTimeField(blank=True, null=True, editable=False)

    class Meta:
        """
        Metadata options for the HIT object model.
        """
        ordering = ('id', 'hit_id', 'language_pair', 'block_id')
        verbose_name = "HIT instance"
        verbose_name_plural = "HIT instances"

    # pylint: disable-msg=E1002
    def __init__(self, *args, **kwargs):
        """
        Makes sure that self.hit_attributes are available.
        """
        super(HIT, self).__init__(*args, **kwargs)

        if not self.hit_id:
            self.hit_id = self.__class__._create_hit_id()

        # If a hit_xml file is available, populate self.hit_attributes.
        self.reload_dynamic_fields()

    def __unicode__(self):
        """
        Returns a Unicode String for this HIT object.
        """
        return u'<HIT id="{0}" hit="{1}" block="{2}" language-pair="{3}">' \
          .format(self.id, self.hit_id, self.block_id, self.language_pair)

    @classmethod
    def _create_hit_id(cls):
        """Creates a random UUID-4 8-digit hex number for use as HIT id."""
        new_id = uuid.uuid4().hex[:8]
        while cls.objects.filter(hit_id=new_id):
            new_id = uuid.uuid4().hex[:8]

        return new_id

    @classmethod
    def compute_remaining_hits(cls, language_pair=None):
        """
        Computes the number of remaining HITs in the database.

        If language_pair is given, it constraints on the HITs' language pair.

        """
        hits_qs = cls.objects.filter(active=True, mturk_only=False, completed=False)
        if language_pair:
            hits_qs = hits_qs.filter(language_pair=language_pair)

        available = 0
        for hit in hits_qs:
            # Before we checked if `hit.users.count() < 3`.
            if hit.users.count() < MAX_USERS_PER_HIT:
                available = available + 1

            # Set active HITs to completed if there exists at least one result.
            else:
                hit.completed = True
                hit.save()

        return available

    @classmethod
    def compute_status_for_user(cls, user, project=None, language_pair=None):
        """
        Computes the HIT completion status for the given user.

        If project is given, it constraints on the HITs' project.
        If language_pair is given, it constraints on the HITs' language pair.

        Returns a list containing:

        - number of completed HITs;
        - average duration per HIT in seconds;
        - total duration in seconds.

        """
        hits_qs = cls.objects.filter(users=user)
        if project:
            project_instance = Project.objects.filter(id=project.id)
            if project_instance.exists():
                hits_qs = hits_qs.filter(project=project_instance[0])
            else:
                return [0, 0, 0]

        if language_pair:
            hits_qs = hits_qs.filter(language_pair=language_pair)

        _completed_hits = hits_qs.count()

        _durations = []
        for hit in hits_qs:
            _results = RankingResult.objects.filter(user=user, item__hit=hit)
            _durations.extend(_results.values_list('duration', flat=True))

        _durations = [datetime_to_seconds(d) for d in _durations if d]
        _total_duration = sum(_durations)
        _average_duration = _total_duration / float(_completed_hits or 1)

        current_status = []
        current_status.append(_completed_hits)
        current_status.append(_average_duration)
        current_status.append(_total_duration)

        return current_status

    @classmethod
    def compute_status_for_group(cls, group, project=None, language_pair=None):
        """
        Computes the HIT completion status for users of the given group.
        """
        combined = [0, 0, 0]
        for user in group.user_set.all():
            _user_status = cls.compute_status_for_user(user, project, language_pair)
            combined[0] = combined[0] + _user_status[0]
            combined[1] = combined[1] + _user_status[1]
            combined[2] = combined[2] + _user_status[2]

        combined[1] = combined[2] / float(combined[0] or 1)
        return combined

    # pylint: disable-msg=E1002
    def save(self, *args, **kwargs):
        """
        Makes sure that validation is run before saving an object instance.
        """
        # Enforce validation before saving HIT objects.
        if not self.id:
            self.full_clean()

            # We have to call save() here to get an id for this instance.
            super(HIT, self).save(*args, **kwargs)

            _tree = fromstring(self.hit_xml.encode("utf-8"))

            for _child in _tree:
                new_item = RankingTask(hit=self, item_xml=tostring(_child))
                new_item.save()

        # Check ranking tasks to update
        try:
            related_result = RankingResult.objects.filter(item__hit=self).latest('completion')
            self.finished = related_result.completion

        except RankingResult.DoesNotExist:
            pass

        super(HIT, self).save(*args, **kwargs)

    def get_absolute_url(self):
        """
        Returns the URL for this HIT object instance.
        """
        hit_handler_view = 'appraise.cs_rest.views.hit_handler'
        kwargs = {'hit_id': self.hit_id}
        return reverse(hit_handler_view, kwargs=kwargs)

    def get_status_url(self):
        """
        Returns the status URL for this HIT object instance.
        """
        status_handler_view = 'appraise.cs_rest.views.status_view'
        kwargs = {'hit_id': self.hit_id}
        return reverse(status_handler_view, kwargs=kwargs)

    def reload_dynamic_fields(self):
        """
        Reloads hit_attributes from self.hit_xml contents.
        """
        # If a hit_xml file is available, populate self.hit_attributes.
        if self.hit_xml:
            try:
                _hit_xml = fromstring(self.hit_xml.encode("utf-8"))
                self.hit_attributes = {}
                for key, value in _hit_xml.attrib.items():
                    self.hit_attributes[key] = value

            # For parse errors, set self.hit_attributes s.t. it gives an
            # error message to the user for debugging.
            except (ParseError), msg:
                self.hit_attributes = {'note': msg}

    def export_to_xml(self):
        """
        Renders this HIT as XML String.
        """
        template = get_template('cs_rest/task_result.xml')

        # If a hit_xml file is available, populate self.hit_attributes.
        self.reload_dynamic_fields()

        _attr = self.hit_attributes.items()
        attributes = ' '.join(['{}="{}"'.format(k, v) for k, v in _attr])

        results = []
        for item in RankingTask.objects.filter(hit=self):
            item.reload_dynamic_fields()

            try:
                source_id = item.source[1]["id"]
            except:
                source_id = -1

            _results = []
            for _result in item.rankingresult_set.all():
                _results.append(_result.export_to_xml())

            results.append((source_id, _results))

        context = {'hit_id': self.hit_id, 'attributes': attributes,
          'results': results}
        return template.render(Context(context))

    def export_to_apf(self):
        """
        Exports this HIT's results to Artstein and Poesio (2007) format.
        """
        results = []
        for item in RankingTask.objects.filter(hit=self):
            for _result in item.rankingresult_set.all():
                _apf_output = _result.export_to_apf()
                if _apf_output:
                    results.append(_apf_output)
        return u"\n".join(results)


    def compute_agreement_scores(self):
        """
        Computes alpha, kappa, pi and Bennett's S agreement scores using NLTK.
        """
        _raw = self.export_to_apf()
        if not _raw:
            return None
        else:
            _raw = _raw.split('\n')

        # Convert raw results data into data triples and create a new
        # AnnotationTask object for computation of agreement scores.
        _data = [_line.split(',') for _line in _raw]
        try:
            _data = [(x[0], x[1], x[2]) for x in _data]

        except IndexError:
            return None

        # Compute alpha, kappa, pi, and S scores.
        _task = AnnotationTask(data=_data)
        try:
            _alpha = _task.alpha()
            _kappa = _task.kappa()
            _pi = _task.pi()
            # pylint: disable-msg=C0103
            _S = _task.S()

        except ZeroDivisionError, msg:
            LOGGER.debug(msg)
            return None

        return (_alpha, _kappa, _pi, _S)


class Project(models.Model):
    """
    Defines object model for an annotation project
    """
    # Project names are string-based and should match regex [a-zA-Z0-9\-]{1,100}
    name = models.CharField(
      blank=False,
      db_index=True,
      max_length=100,
      null=False,
      unique=True,
      validators=[RegexValidator(regex=r'[a-zA-Z0-9\-]{1,100}')],
    )

    # Users working on this project
    users = models.ManyToManyField(
      User,
      blank=True,
      db_index=True,
      null=True,
    )

    # HITs belonging to this project
    HITs = models.ManyToManyField(
      HIT,
      blank=True,
      db_index=True,
      null=True,
    )

    def __str__(self):
        return '<project id="{0}" name="{1}" users="{2}" HITs="{3}" />'.format(self.id, self.name, self.users.count(), self.HITs.count())


class RankingTask(models.Model):
    """
    RankingTask object model for cs_rest ranking evaluation.
    """
    hit = models.ForeignKey(
      HIT,
      db_index=True
    )

    item_xml = models.TextField(
      help_text="XML source for this RankingTask instance.",
      validators=[validate_segment_xml],
      verbose_name="RankingTask source XML"
    )

    # These fields are derived from item_xml and NOT stored in the database.
    attributes = None
    source = None
    reference = None
    translations = None

    class Meta:
        """
        Metadata options for the RankingTask object model.
        """
        ordering = ('id',)
        verbose_name = "RankingTask instance"
        verbose_name_plural = "RankingTask instances"

    # pylint: disable-msg=E1002
    def __init__(self, *args, **kwargs):
        """
        Makes sure that self.translations are available.
        """
        super(RankingTask, self).__init__(*args, **kwargs)

        # If item_xml is available, populate dynamic fields.
        self.reload_dynamic_fields()

    def __unicode__(self):
        """
        Returns a Unicode String for this RankingTask object.
        """
        return u'<ranking-task id="{0}">'.format(self.id)

    # pylint: disable-msg=E1002
    def save(self, *args, **kwargs):
        """
        Makes sure that validation is run before saving an object instance.
        """
        # Enforce validation before saving RankingTask objects.
        self.full_clean()

        super(RankingTask, self).save(*args, **kwargs)

    def reload_dynamic_fields(self):
        """
        Reloads source, reference, and translations from self.item_xml.
        """
        if self.item_xml:
            try:
                _item_xml = fromstring(self.item_xml)

                self.attributes = _item_xml.attrib

                _source = _item_xml.find('source')
                if _source is not None:
                    self.source = (_source.text, _source.attrib)

                _reference = _item_xml.find('reference')
                if _reference is not None:
                    self.reference = (_reference.text, _reference.attrib)

                self.translations = []
                for _translation in _item_xml.iterfind('translation'):
                    self.translations.append((_translation.text,
                      _translation.attrib))

            except ParseError:
                self.source = None
                self.reference = None
                self.translations = None


class RankingResult(models.Model):
    """
    Evaluation Result object model.
    """
    item = models.ForeignKey(
      RankingTask,
      db_index=True
    )

    user = models.ForeignKey(
      User,
      db_index=True
    )

    duration = models.TimeField(blank=True, null=True, editable=False)

    completion = models.DateTimeField(auto_now_add=True, blank=True, null=True, editable=False)

    def readable_duration(self):
        """
        Returns a readable version of the this RankingResult's duration.
        """
        return '{}'.format(self.duration)

    raw_result = models.TextField(editable=False, blank=False)

    results = None

    systems = 0

    class Meta:
        """
        Metadata options for the RankingResult object model.
        """
        ordering = ('id',)
        verbose_name = "RankingResult object"
        verbose_name_plural = "RankingResult objects"

    # pylint: disable-msg=E1002
    def __init__(self, *args, **kwargs):
        """
        Makes sure that self.results are available.
        """
        super(RankingResult, self).__init__(*args, **kwargs)

        # If raw_result is available, populate dynamic field.
        self.reload_dynamic_fields()

    def __unicode__(self):
        """
        Returns a Unicode String for this RankingResult object.
        """
        return u'<ranking-result id="{0}">'.format(self.id)

    def reload_dynamic_fields(self):
        """
        Reloads source, reference, and translations from self.item_xml.
        """
        if self.raw_result and self.raw_result != 'SKIPPED':
            try:
                self.results = self.raw_result.split(',')
                self.results = [int(x) for x in self.results]

                self.systems = sum([len(x[1]['system'].split(',')) for x in self.item.translations])

            # pylint: disable-msg=W0703
            except Exception, msg:
                self.results = msg

    def export_to_xml(self):
        """
        Renders this RankingResult as XML String.
        """
        return self.export_to_ranking_xml()

    def export_to_ranking_xml(self):
        """
        Renders this RankingResult as Ranking XML String.
        """
        template = get_template('cs_rest/ranking_result.xml')

        _attr = self.item.attributes.items()
        attributes = ' '.join(['{}="{}"'.format(k, v) for k, v in _attr])

        skipped = self.results is None

        translations = []
        if not skipped:
            for index, translation in enumerate(self.item.translations):
                _items = translation[1].items()
                _attr = ' '.join(['{}="{}"'.format(k, v) for k, v in _items])
                _rank = self.results[index]
                translations.append((_attr, _rank))

        context = {
          'attributes': attributes,
          'duration': '{}'.format(self.duration),
          'skipped': skipped,
          'translations': translations,
          'user': self.user,
        }

        return template.render(Context(context))


    def export_to_pairwise_csv(self):
        """
        Renders this RankingResult as pairwise CSV String.

        Format:
        srclang,trglang,srcIndex,segmentId,judgeID,system1Id,system1rank,system2Id,system2rank,rankingID

        """
        skipped = self.results is None
        if skipped:
            return None

        try:
            srcIndex = self.item.source[1]["id"]
        except:
            srcIndex = -1

        _src_lang = self.item.hit.hit_attributes['source-language']
        _trg_lang = self.item.hit.hit_attributes['target-language']

        csv_data = []
        csv_data.append(ISO639_3_TO_NAME_MAPPING[_src_lang]) # srclang
        csv_data.append(ISO639_3_TO_NAME_MAPPING[_trg_lang]) # trglang
        csv_data.append(srcIndex)                            # srcIndex
        csv_data.append(srcIndex)                            # segmentId
        csv_data.append(self.user.username)                  # judgeID

        base_values = csv_data

        systems = set()
        for index, translation in enumerate(self.item.translations):
            name = translation[1]['system'].replace(',', '+')
            rank = self.results[index]
            systems.add((name, rank))

        csv_output = []
        from itertools import combinations
        for (sysA, sysB) in combinations(systems, 2):
            # Compute all systems in sysA, sysB which can be multi systems
            expandedA = sysA[0].split('+')
            expandedB = sysB[0].split('+')

            # Pairwise comparisons without intra-multi-system pairs
            for singleA in expandedA:
                for singleB in expandedB:
                    csv_local = []
                    csv_local.extend(base_values)
                    csv_local.append(singleA)               # system1Id
                    csv_local.append(str(sysA[1]))          # system1rank
                    csv_local.append(singleB)               # system2Id
                    csv_local.append(str(sysB[1]))          # system2rank
                    csv_local.append(str(self.item.id))     # rankingID
                    csv_joint = u",".join(csv_local)
                    if not csv_joint in csv_output:
                        csv_output.append(csv_joint)

            # Intra-multi-system pairs, sharing the same rank
            # We'll only add these once to prevent duplicate entries
            if len(expandedA) > 1:
                for (singleA1, singleA2) in combinations(expandedA, 2):
                    csv_local = []
                    csv_local.extend(base_values)
                    csv_local.append(singleA1)              # system1Id
                    csv_local.append(str(sysA[1]))          # system1rank
                    csv_local.append(singleA2)              # system2Id
                    csv_local.append(str(sysA[1]))          # system2rank
                    csv_local.append(str(self.item.id))     # rankingID
                    csv_joint = u",".join(csv_local)
                    if not csv_joint in csv_output:
                        csv_output.append(csv_joint)

            # Intra-multi-system pairs, sharing the same rank
            # We'll only add these once to prevent duplicate entries
            if len(expandedB) > 1:
                for (singleB1, singleB2) in combinations(expandedB, 2):
                    csv_local = []
                    csv_local.extend(base_values)
                    csv_local.append(singleB1)              # system1Id
                    csv_local.append(str(sysB[1]))          # system1rank
                    csv_local.append(singleB2)              # system2Id
                    csv_local.append(str(sysB[1]))          # system2rank
                    csv_local.append(str(self.item.id))     # rankingID
                    csv_joint = u",".join(csv_local)
                    if not csv_joint in csv_output:
                        csv_output.append(csv_joint)

        return u"\n".join(csv_output)


    def export_to_ranking_csv(self):
        """
        Renders this RankingResult as Ranking CSV String.

        Format:
        ID,srcLang,tgtLang,user,duration,rank_1,word_count_1,rank_2,word_count_2,rank_3,word_count_3,rank_4,word_count_5,rank_1,word_count_5

        """
        # TODO: this needs to be cleaned up...
        # We'd like to have a minimal version of the ranking CSV output.
        # Not sure why this one generates ranks and word counts... :)
        raise NotImplementedError("not ready yet")
        ranking_csv_data = []

        try:
            ranking_csv_data.append(self.item.source[1]["id"])
        except:
            ranking_csv_data.append(-1)

        _src_lang = self.item.hit.hit_attributes['source-language']
        _trg_lang = self.item.hit.hit_attributes['target-language']

        ranking_csv_data.append(ISO639_3_TO_NAME_MAPPING[_src_lang]) # srclang
        ranking_csv_data.append(ISO639_3_TO_NAME_MAPPING[_trg_lang]) # trglang

        ranking_csv_data.append(self.user.username)
        ranking_csv_data.append(str(datetime_to_seconds(self.duration)))

        skipped = self.results is None

        translations = []
        if not skipped:
            for index, translation in enumerate(self.item.translations):
                _word_count = len(translation[0].split())
                _rank = self.results[index]
                translations.append((_rank, _word_count))

        for rank, word_count in translations:
            ranking_csv_data.append(str(rank))
            ranking_csv_data.append(str(word_count))

        return u",".join(ranking_csv_data)


    def export_to_csv(self, expand_multi_systems=False):
        """
        Exports this RankingResult in CSV format.
        """
        item = self.item
        hit = self.item.hit
        values = []

        _src_lang = hit.hit_attributes['source-language']
        _trg_lang = hit.hit_attributes['target-language']

        # TODO: this relies on the fact that we have five systems per HIT.
        #   To resolve this, we might have to skip systems detection based
        #   on the HIT attribute and instead process the translations.
        #
        # System ids can be retrieved from HIT or segment level.
        #
        # We cannot do this anymore as we might have multi-systems.
        #if 'systems' in hit.hit_attributes.keys():
        #    _systems = hit.hit_attributes['systems'].split(',')

        # See below for a potential implementation to address multi-systems.
        #
        # On segment level, we have to extract the individual "system" values
        # from the <translation> attributes which are stored in the second
        # position of the translation tuple: (text, attrib).
        _systems = []
        for translation in item.translations:
            _systems.append(translation[1]['system'])

        # Note that srcIndex and segmentId are 1-indexed for compatibility
        # with evaluation scripts from previous editions of the WMT.
        values.append(ISO639_3_TO_NAME_MAPPING[_src_lang]) # srclang
        values.append(ISO639_3_TO_NAME_MAPPING[_trg_lang]) # trglang
        values.append(item.source[1]['id'])                # srcIndex
        values.append('-1')                                # documentId
        values.append(item.source[1]['id'])                # segmentId (= srcIndex)
        values.append(self.user.username)                  # judgeId

        # Save current data values as we might have to write them out
        # several times when multi-systems trigger multiple results...
        base_values = values

        # Don't fail for skipped items
        if not self.results:
            self.results = [-1] * len(_systems)

        _system_names = []
        _system_ranks = []
        for _result_index, _system in enumerate(_systems):
            if expand_multi_systems:
                _local_systems = _system.split(',')
                _local_results = [str(self.results[_result_index])] * len(_local_systems)
                _system_names.extend(_local_systems)
                _system_ranks.extend(_local_results)
            else:
                _system_names.append(_system.replace(',', '+'))
                _system_ranks.append(str(self.results[_result_index]))

        # Check if we need to add placeholder systems to pad to 5*k systems.
        # This is needed as our export format expects five systems per line.
        if len(_system_names) % 5 > 0:
            _missing_systems = 5 - len(_system_names) % 5
            for x in range(_missing_systems):
                _system_names.append('PLACEHOLDER')
                _system_ranks.append('-1')

        all_values = []
        for _base_index in range(len(_system_names))[::5]:
            current_values = list(base_values)
            current_ranks = []
            for _current_index in range(len(_system_names))[_base_index:_base_index+5]:
                current_values.append('-1')
                current_values.append(str(_system_names[_current_index]))
                current_ranks.append(_system_ranks[_current_index])
            current_values.extend(current_ranks)
            all_values.append(u",".join(current_values))

        # This does not work anymore as we face multi-systems.
        #
        #values.append('-1')                                # system1Number
        #values.append(str(_systems[0]))                    # system1Id
        #values.append('-1')                                # system2Number
        #values.append(str(_systems[1]))                    # system2Id
        #values.append('-1')                                # system3Number
        #values.append(str(_systems[2]))                    # system3Id
        #values.append('-1')                                # system4Number
        #values.append(str(_systems[3]))                    # system4Id
        #values.append('-1')                                # system5Number
        #values.append(str(_systems[4]))                    # system5Id
        #
        # TODO: decide what happens in case of k>5 systems due to
        #   multi-systems.  Can we simply add annother CSV line and
        #   add the extra system rankings?  If so, we should define
        #   a "dummy" system to make sure we don't break CSV format.
        #
        #   Specifying a value of -1 for system rank should work...
        #
        # system1rank,system2rank,system3rank,system4rank,system5rank
        #if self.results:
        #    values.extend([str(x) for x in self.results])
        #else:
        #    values.extend(['-1'] * 5)

        return u"\n".join(all_values)


    # pylint: disable-msg=C0103
    def export_to_apf(self):
        """
        Exports this RankingResult to Artstein and Poesio (2007) format.
        """
        if not self.results:
            return None

        item = self.item
        hit = self.item.hit

        _systems = []
        # System ids can be retrieved from HIT or segment level.
        #
        # We cannot do this anymore as we might have multi-systems.
        # if 'systems' in hit.hit_attributes.keys():
        #    _systems = hit.hit_attributes['systems'].split(',')

        # On segment level, we have to extract the individual "system" values
        # from the <translation> attributes which are stored in the second
        # position of the translation tuple: (text, attrib).
        for translation in item.translations:
            _systems.append(translation[1]['system'])

        from itertools import combinations, product
        results = []

        # TODO: this relies on the fact that we have five systems per HIT.
        #   To resolve this, we might have to skip systems detection based
        #   on the HIT attribute and instead process the translations.
        #
        #   An additional problem is that we might have multi-systems.
        #   These occur when two systems had the same translation output
        #   during batch creation.  Such cases will spawn additional
        #   result items when multi-systems get expanded into individual
        #   units.  This may happen for both sides, e.g., systems A, B.
        #
        # Note that srcIndex is 1-indexed for compatibility with evaluation
        # scripts from previous editions of the WMT.
        for a, b in combinations(range(5), 2):
            _c = self.user.username
            _i = '{0}.{1}.{2}'.format(item.source[1]['id'], a+1, b+1)

            # Determine individual systems for multi-system entries.
            _individualA = _systems[a].split(',')
            _individualB = _systems[b].split(',')

            for _systemA, _systemB in product(_individualA, _individualB):
                _verdict = '?'
                if self.results[a] > self.results[b]:
                    _verdict = '>'
                elif self.results[a] < self.results[b]:
                    _verdict = '<'
                elif self.results[a] == self.results[b]:
                    _verdict = '='

                _v = '{0}{1}{2}'.format(str(_systemA), _verdict, str(_systemB))

                results.append('{0},{1},{2}'.format(_c, _i, _v))

        return u'\n'.join(results)


@receiver(models.signals.post_save, sender=RankingResult)
def update_user_hit_mappings(sender, instance, created, **kwargs):
    """
    Updates the User/Project/HIT mappings.
    """
    hit = instance.item.hit
    user = instance.user
    results = RankingResult.objects.filter(user=user, item__hit=hit)

    if len(results) > 2:
        from appraise.cs_rest.views import _compute_next_task_for_user
        LOGGER.debug('Deleting stale User/HIT mapping {0}->{1}'.format(
          user, hit))
        hit.users.add(user)
        for project in hit.project_set.all():
            UserHITMapping.objects.filter(user=user, project=project, hit=hit).delete()
            _compute_next_task_for_user(user, project, hit.language_pair)

@receiver(models.signals.post_delete, sender=RankingResult)
def remove_user_from_hit(sender, instance, **kwargs):
    """
    Removes user from list of users who have completed corresponding HIT.
    """
    user = instance.user

    try:
        hit = instance.item.hit

        LOGGER.debug('Removing user "{0}" from HIT {1}'.format(user, hit))
        hit.users.remove(user)

        from appraise.cs_rest.views import _compute_next_task_for_user
        _compute_next_task_for_user(user, hit.project, hit.language_pair)

    except (HIT.DoesNotExist, RankingTask.DoesNotExist):
        pass


# pylint: disable-msg=E1101
class UserHITMapping(models.Model):
    """
    Object model mapping users to their current HIT instances.
    """
    user = models.ForeignKey(
      User,
      db_index=True
    )

    project = models.ForeignKey(
      Project,
      db_index=True
    )

    hit = models.ForeignKey(
      HIT,
      db_index=True
    )

    class Meta:
        """
        Metadata options for the UserHITMapping object model.
        """
        verbose_name = "User/Project/HIT mapping instance"
        verbose_name_plural = "User/Project/HIT mapping instances"

    def __unicode__(self):
        """
        Returns a Unicode String for this UserHITMapping object.
        """
        return u'<hitmap id="{0}" user="{1}" project="{2}" hit="{3}">'.format(self.id,
          self.user.username, self.project.name, self.hit.hit_id)

    # pylint: disable-msg=E1002
    def save(self, *args, **kwargs):
        """
        Makes sure that HIT's assigned field is updated.
        """
        self.hit.assigned = datetime.now()
        self.hit.save()

        super(UserHITMapping, self).save(*args, **kwargs)


# pylint: disable-msg=E1101
class UserInviteToken(models.Model):
    """
    User invite tokens allowing to register an account.
    """
    group = models.ForeignKey(
      Group,
      db_index=True
    )

    token = models.CharField(
      max_length=8,
      db_index=True,
      default=lambda: UserInviteToken._create_token(),
      unique=True,
      help_text="Unique invite token",
      verbose_name="Invite token"
    )

    active = models.BooleanField(
      db_index=True,
      default=True,
      help_text="Indicates that this invite can still be used.",
      verbose_name="Active?"
    )

    class Meta:
        """
        Metadata options for the UserInviteToken object model.
        """
        verbose_name = "User invite token"
        verbose_name_plural = "User invite tokens"

    # pylint: disable-msg=E1002
    def __init__(self, *args, **kwargs):
        """
        Makes sure that self.token is properly set up.
        """
        super(UserInviteToken, self).__init__(*args, **kwargs)

        if not self.token:
            self.token = self.__class__._create_token()

    def __unicode__(self):
        """
        Returns a Unicode String for this UserInviteToken object.
        """
        return u'<user-invite id="{0}" token="{1}" active="{2}">'.format(
          self.id, self.token, self.active)

    @classmethod
    def _create_token(cls):
        """Creates a random UUID-4 8-digit hex number for use as a token."""
        new_token = uuid.uuid4().hex[:8]
        while cls.objects.filter(token=new_token):
            new_token = uuid.uuid4().hex[:8]

        return new_token


class TimedKeyValueData(models.Model):
    """
    Stores a simple (key, value) pair.
    """
    key = models.CharField(max_length=100, blank=False, null=False)
    value = models.TextField(blank=False, null=False)
    date_and_time = models.DateTimeField(blank=False, null=False, editable=False, auto_now_add=True)

    @classmethod
    def update_status_if_changed(cls, key, new_value):
        """
        Stores a new TimedKeyValueData instance if value for key has changed
        """
        _latest_values = cls.objects.filter(key=key).order_by('date_and_time').reverse().values_list('value', flat=True)
        if not _latest_values or _latest_values[0] != new_value:
            new_data = cls(key=key, value=new_value)
            new_data.save()


def initialize_database():
    """
    Initializes database with required language code and CS_REST groups
    """
    researcher_group_names = set(GROUP_HIT_REQUIREMENTS.keys())
    for researcher_group_name in researcher_group_names:
        LOGGER.debug("Validating researcher group '{0}'".format(researcher_group_name))
        _ = Group.objects.get_or_create(name=researcher_group_name)
    language_pair_codes = set(x[0] for x in LANGUAGE_PAIR_CHOICES)
    for language_pair_code in language_pair_codes:
        LOGGER.debug("Validating group '{0}'".format(language_pair_code))
        _ = Group.objects.get_or_create(name=language_pair_code)
    LOGGER.debug("Validating group 'CS_REST'")
    _ = Group.objects.get_or_create(name='CS_REST')
