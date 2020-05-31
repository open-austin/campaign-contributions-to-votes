import graphene
from graphene import relay, Node
from graphene_django import DjangoObjectType
from graphene_django.filter import DjangoFilterConnectionField
from django.db import connection, transaction
from django.db.models import Count
from graphene_django.debug import DjangoDebug
from influencetx.legislators.models import Legislator, LegislatorIdMap
from influencetx.tpj.models import Donor, Contribution, Contributionsummary, Filer
from influencetx.bills.models import Bill, ActionDate, VoteTally, SingleVote, SubjectTag
from promise import Promise
from promise.dataloader import DataLoader

import logging
log = logging.getLogger(__name__)


class BillLoader(DataLoader):
    def batch_load_fn(self, keys):
        bills = {bill.id: bill for bill in Bill.objects.filter(id__in=keys)}
        return Promise.resolve([bills.get(bill_id) for bill_id in keys])
bill_loader = BillLoader()

## default surprisingly does not have total count
class ExtendedConnection(graphene.Connection):
    class Meta:
        abstract = True

    total_count = graphene.Int()
    def resolve_total_count(self, info, **kwargs):
        return self.length

class DonorExtendedConnection(graphene.Connection):
    class Meta:
        abstract = True

    total_count = graphene.Int()
    def resolve_total_count(self, info, **kwargs):
        if "inState" in info.variable_values:
            if (info.variable_values["inState"] == True):
                return execute_raw_query("""SELECT COUNT(*) FROM "contributors" WHERE "StateAbbr" = 'TX';""")[0]["count"]
            if (info.variable_values["inState"] == False):
                return execute_raw_query("""SELECT COUNT(*) FROM "contributors" WHERE "StateAbbr" != 'TX';""")[0]["count"]

        return execute_raw_query('SELECT COUNT(*) AS "__count" FROM "contributors"')[0]["__count"]

class BillType(DjangoObjectType):
    class Meta:
        model = Bill
        filter_fields = {
            'title': ['exact', 'icontains', 'istartswith'],
            'chamber': ['exact', 'icontains'],
        }
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection
        def get_stats(self):
            return []
    pk = graphene.Int()
    def resolve_pk(self, info, **kwargs):
        return self.id
    def resolve_bills_sponsored(self, info):
        return bill_loader.load(self.bills_sponsored)

class DonationType(graphene.ObjectType):
    cycle_total = graphene.Field(graphene.Int)
    leg_id = graphene.Field(graphene.Int)
    party = graphene.Field(graphene.String)
    candidate_name = graphene.Field(graphene.String)
    office = graphene.Field(graphene.String)

class DonorType(DjangoObjectType):
    class Meta:
        model = Donor
        filter_fields = {
            'id': ['exact', 'icontains', 'istartswith'],
            'full_name': ['exact', 'icontains'],
            'state': ['exact', 'icontains'],
        }
        interfaces = (relay.Node, )
        connection_class = DonorExtendedConnection
    pk = graphene.Int()
    def resolve_pk(self, info, **kwargs):
        return self.id

    donations_count = graphene.Int()
    def resolve_donations_count(self, info, **kwargs):
        return execute_raw_query("""
            SELECT COUNT("iFILER_ID")
            FROM total_donorbyfiler_2018 
            INNER JOIN filers ON "filers"."iFILER_ID" = "total_donorbyfiler_2018"."ifiler_ID" 
            WHERE "total_donorbyfiler_2018"."ctrib_ID"=""" 
            + str(self.id) + ';'
        )[0]["count"]

    donations = graphene.List(DonationType)
    def resolve_donations(self, info, **kwargs):
        return execute_raw_query("""
            SELECT cycle_total, party, "legislators_legislator"."id" as leg_id, "CandidateName" as candidate_name, "Office" as office 
            FROM total_donorbyfiler_2018 
            INNER JOIN filers ON "filers"."iFILER_ID" = "total_donorbyfiler_2018"."ifiler_ID" 
            LEFT JOIN legislators_legislatoridmap ON "legislators_legislatoridmap"."tpj_filer_id" = "total_donorbyfiler_2018"."ifiler_ID" 
            LEFT JOIN legislators_legislator ON "legislators_legislator"."openstates_leg_id" = "legislators_legislatoridmap"."openstates_leg_id" 
            WHERE "total_donorbyfiler_2018"."ctrib_ID"=""" 
            + str(self.id) + ' ORDER BY cycle_total DESC limit 500;'
        )
        
    def donorsummarys(self, info, **kwargs):
        return self.donorsummarys.prefetch_related('filer').order_by('-cycle_total')[:25]

class SubjectTagType(DjangoObjectType):
    class Meta:
        model = SubjectTag
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection

class VoteTallyType(DjangoObjectType):
    class Meta:
        model = VoteTally
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection


class ActionDateType(DjangoObjectType):
    class Meta:
        model = ActionDate
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection

class SingleVoteType(DjangoObjectType):
    class Meta:
        model = SingleVote
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection

class ContributionType(DjangoObjectType):
    class Meta:
        model = Contribution
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection
        
class FilerType(DjangoObjectType):
    class Meta:
        model = Filer
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection
    
    legislator = graphene.Field(lambda: LegislatorType)
    def resolve_legislator(self, info, **kwargs):
        try:
            id_map = LegislatorIdMap.objects.get(tpj_filer_id=self.id)
            return Legislator.objects.get(openstates_leg_id=id_map.openstates_leg_id)
        except:
            return None

class ContributionsummaryType(DjangoObjectType):
    class Meta:
        model = Contributionsummary
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection

    filer = graphene.Field(FilerType)
    def resolve_filer(self, info, **kwargs):
        return self.filer

class FilterCountType(graphene.ObjectType):
    count = graphene.Field(graphene.Int)
    name = graphene.Field(graphene.String)
    average_order = graphene.Field(graphene.Float)

class ContributionssummaryTypeConnection(ExtendedConnection):
    class Meta:
        node = ContributionsummaryType

class LegislatorType(DjangoObjectType):
    class Meta:
        model = Legislator
        filter_fields = {
            'name': ['exact', 'icontains', 'istartswith'],
            'chamber': ['exact', 'icontains'],
            'district': ['exact'],
            'party': ['exact', 'icontains'],
        }
        order_fields = ('name', 'name')
        interfaces = (relay.Node, )
        connection_class = ExtendedConnection

    pk = graphene.Int()
    def resolve_pk(self, info, **kwargs):
        return self.id
    
    contributions = relay.ConnectionField(ContributionssummaryTypeConnection)

    def resolve_contributions(self, info, **kwargs):
        try:
            id_map = LegislatorIdMap.objects.get(
                openstates_leg_id=self.openstates_leg_id)
            filer = Filer.objects.filter(id=id_map.tpj_filer_id).first()
            return Contributionsummary.objects.select_related(
                'donor').filter(filer=filer.id).order_by('-cycle_total')
        except:
            return []

def execute_raw_query(sql_string):
    cursor = connection.cursor()
    cursor.execute(sql_string)
    results = cursor.fetchall()
    list = []
    i = 0
    for row in results:
        dict = {} 
        field = 0
        while True:
            try:
                if(results[i][field]):
                    dict[cursor.description[field][0]] = str(results[i][field])
                field = field +1
            except IndexError as e:
                break
        i = i + 1
        list.append(dict) 
    return list

def CustomBillFilters(classification, party, multiple_sponsors, chamber=""):
    bills = Bill.objects
    if chamber:
        bills = bills.filter(chamber__icontains=chamber)
    if classification:
        if(classification == "filing"):
            bills = bills.exclude(action_dates__classification="committee-passage")
        if(classification == "committee-passage"):
            bills = bills.filter(action_dates__classification="committee-passage").exclude(action_dates__classification="became-law")
        if(classification == "became-law"):
            bills = bills.filter(action_dates__classification="became-law").exclude(action_dates__classification="committee-passage")
        bills.filter(action_dates__classification__in=classification)
    if party:
        if(party == "D"):
            bills = bills.filter(sponsors__party="D").exclude(sponsors__party="R")
        if(party == "R"):
            bills = bills.filter(sponsors__party="R").exclude(sponsors__party="D")
        if(party == "Bipartisan"):
            bills = bills.filter(sponsors__party="R").filter(sponsors__party="D")
    if multiple_sponsors:
        bills = bills.annotate(num_sponsors=Count('sponsors')).filter(num_sponsors__gte=2)
    return bills

class Query(graphene.ObjectType):
    debug = graphene.Field(DjangoDebug, name='_debug')
    bill = graphene.Field(BillType, pk=graphene.Int())
    def resolve_bill(self, info, **kwargs):
        pk = kwargs.get('pk')
        return Bill.objects.get(pk=pk)

    bills = DjangoFilterConnectionField(
        BillType, 
        classification=graphene.Argument(graphene.String),
        party=graphene.Argument(graphene.String),
        multiple_sponsors=graphene.Argument(graphene.Boolean)
    )
    def resolve_bills(self, info, **kwargs):
        bills = CustomBillFilters(
            classification=kwargs.get("classification"), 
            party=kwargs.get("party"), 
            multiple_sponsors=kwargs.get("multiple_sponsors")
        )

        return bills.distinct()

    bill_classification_stats = graphene.List(
        FilterCountType,
        chamber=graphene.Argument(graphene.String),
        party=graphene.Argument(graphene.String),
        multiple_sponsors=graphene.Argument(graphene.Boolean)
    )
    def resolve_bill_classification_stats(self, info, **kwargs):
        bills = CustomBillFilters(
            chamber=kwargs.get("chamber"),
            classification=kwargs.get("classification"),
            party=kwargs.get("party"), 
            multiple_sponsors=kwargs.get("multiple_sponsors")
        )
        return [
            {"name": "filing", "count": bills.exclude(action_dates__classification="committee-passage").count()},
            {"name": "committee-passage", "count": bills.filter(action_dates__classification="committee-passage").exclude(action_dates__classification="became-law").count()},
            {"name": "became-law", "count": bills.filter(action_dates__classification="became-law").exclude(action_dates__classification="committee-passage").count()},
        ]

    legislator = graphene.Field(LegislatorType, pk=graphene.Int())
    def resolve_legislator(self, info, **kwargs):
        pk = kwargs.get('pk')
        return Legislator.objects.get(pk=pk)

    legislators = DjangoFilterConnectionField(LegislatorType)
    def resolve_legislators(self, info, **kwargs):
        return Legislator.objects.all().order_by('district')

    donor = graphene.Field(DonorType, pk=graphene.Int())
    def resolve_donor(self, info, **kwargs):
        pk = kwargs.get('pk')
        return Donor.objects.get(pk=pk)

    donors = DjangoFilterConnectionField(DonorType, in_state=graphene.Argument(graphene.Boolean))
    def resolve_donors(self, info, **kwargs):
        donors = Donor.objects.all().order_by('-total_contributions')
        if(kwargs.get('in_state') == True):
            donors = donors.filter(state="TX")
        if(kwargs.get('in_state') == False):
            donors = donors.exclude(state="TX")
        return donors

    donor_state_stats = graphene.List(
        FilterCountType
    )
    def resolve_donor_state_stats(self, info, **kwargs):
        donors = Donor.objects
        return [
            {"name": "in-state", "count": donors.filter(state="TX").count()},
            {"name": "out-of-state", "count": donors.exclude(state="TX").count()},
        ]


schema = graphene.Schema(query=Query)
