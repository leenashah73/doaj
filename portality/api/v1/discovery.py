from portality.api.v1.common import Api
from portality import models
from portality.core import app
from datetime import datetime
import esprit
import re, json, uuid

class DiscoveryException(Exception):
    pass

class SearchResult(object):
    def __init__(self, raw=None):
        self.data = raw if raw is not None else {}

def query_substitute(query, substitutions):
    if len(substitutions.keys()) == 0:
        return query

    # apply the regex escapes to the substitutions, so we know they
    # are ready to be matched
    escsubs = {}
    for k, v in substitutions.iteritems():
        escsubs[k.replace(":", "\\:")] = v

    # define a function which takes the match group and returns the
    # substitution if there is one
    def rep(match):
        for k, v in escsubs.iteritems():
            if k == match.group(1):
                return v
        return match.group(1)

    # define the regular expressions for splitting and then extracting
    # the field to be substituted
    split_rx = "([^\\\\]:)"
    field_rx = "([^\s\+\-\(\)\"]+?):$"

    # split the query around any unescaped colons
    bits = re.split(split_rx, query)

    # stitch back together the split sections and the separators
    segs = [bits[i] + bits[i+1] for i in range(0, len(bits), 2) if i+1 < len(bits)] + [bits[len(bits) - 1]] if len(bits) % 2 == 1 else []

    # substitute the fields as required
    subs = []
    for seg in segs:
        if seg.endswith(":"):
            subs.append(re.sub(field_rx, rep, seg))
        else:
            subs.append(seg)

    return ":".join(subs)

def allowed(query, wildcards=False, fuzzy=False):
    if not wildcards:
        rx = "(.+[^\\\\][\?\*]+.*)"
        if re.search(rx, query):
            return False

    if not fuzzy:
        # this covers both fuzzy searching and proximity searching
        rx = "(.+[^\\\\]~[0-9]{0,1}[\.]{0,1}[0-9]{0,1})"
        if re.search(rx, query):
            return False

    return True

class DiscoveryApi(Api):

    @classmethod
    def _sanitise(cls, q, page, page_size, sort, search_subs, sort_subs):
        if not allowed(q):
            raise DiscoveryException("Query contains disallowed Lucene features")

        q = query_substitute(q, search_subs)
        # print q

        # sanitise the page size information
        if page < 1:
            page = 1

        if page_size > app.config.get("DISCOVERY_MAX_PAGE_SIZE", 100):
            page_size = app.config.get("DISCOVERY_MAX_PAGE_SIZE", 100)
        elif page_size < 1:
            page_size = 10

        # calculate the position of the from cursor in the document set
        fro = (page - 1) * page_size

        # interpret the sort field into the form required by the query
        sortby = None
        sortdir = None
        if sort is not None:
            if ":" in sort:
                bits = sort.split(":")
                if len(bits) != 2:
                    raise DiscoveryException("Malformed sort parameter")

                sortby = bits[0]
                if sortby in sort_subs:
                    sortby = sort_subs[sortby]

                if bits[1] in ["asc", "desc"]:
                    sortdir = bits[1]
                else:
                    raise DiscoveryException("Sort direction must be 'asc' or 'desc'")
            else:
                sortby = sort
                if sortby in sort_subs:
                    sortby = sort_subs[sortby]

        return q, page, fro, page_size, sortby, sortdir

    @classmethod
    def _make_search_query(cls, q, page, page_size, sort, search_subs, sort_subs):
        # sanitise and prep the inputs
        q, page, fro, page_size, sortby, sortdir = cls._sanitise(q, page, page_size, sort, search_subs, sort_subs)

        # assemble the query
        query = SearchQuery(q, fro, page_size, sortby, sortdir)
        # print json.dumps(query.query())

        return query, page, page_size

    @classmethod
    def _make_application_query(cls, account, q, page, page_size, sort, search_subs, sort_subs):
        # sanitise and prep the inputs
        q, page, fro, page_size, sortby, sortdir = cls._sanitise(q, page, page_size, sort, search_subs, sort_subs)

        # assemble the query
        query = ApplicationQuery(account.id, q, fro, page_size, sortby, sortdir)
        # print json.dumps(query.query())

        return query, page, page_size

    @classmethod
    def _make_response(cls, res, q, page, page_size, sort, obs):
        total = res.get("hits", {}).get("total", 0)

        # build the response object
        result = {
            "total" : total,
            "page" : page,
            "pageSize" : page_size,
            "timestamp" : datetime.utcnow().strftime("%Y-%m%dT%H:%M:%SZ"),
            "query" : q,
            "results" : obs
        }

        if sort is not None:
            result["sort"] = sort

        return SearchResult(result)

    @classmethod
    def search_articles(cls, q, page, page_size, sort=None):
        search_subs = app.config.get("DISCOVERY_ARTICLE_SEARCH_SUBS", {})
        sort_subs = app.config.get("DISCOVERY_ARTICLE_SORT_SUBS", {})
        query, page, page_size = cls._make_search_query(q, page, page_size, sort, search_subs, sort_subs)

        # execute the query against the articles
        res = models.Article.query(q=query.query(), consistent_order=False)

        # check to see if there was a search error
        if res.get("error") is not None:
            magic = uuid.uuid1()
            app.logger.error("Error executing discovery query search: {x} (ref: {y})".format(x=res.get("error"), y=magic))
            raise DiscoveryException("There was an error executing your query (ref: {y})".format(y=magic))

        obs = [models.Article(**raw) for raw in esprit.raw.unpack_json_result(res)]
        return cls._make_response(res, q, page, page_size, sort, obs)

    @classmethod
    def search_journals(cls, q, page, page_size, sort=None):
        search_subs = app.config.get("DISCOVERY_JOURNAL_SEARCH_SUBS", {})
        sort_subs = app.config.get("DISCOVERY_JOURNAL_SORT_SUBS", {})
        query, page, page_size = cls._make_search_query(q, page, page_size, sort, search_subs, sort_subs)

        # execute the query against the articles
        res = models.Journal.query(q=query.query(), consistent_order=False)

        # check to see if there was a search error
        if res.get("error") is not None:
            magic = uuid.uuid1()
            app.logger.error("Error executing discovery query search: {x} (ref: {y})".format(x=res.get("error"), y=magic))
            raise DiscoveryException("There was an error executing your query (ref: {y})".format(y=magic))

        obs = [models.Journal(**raw) for raw in esprit.raw.unpack_json_result(res)]
        return cls._make_response(res, q, page, page_size, sort, obs)

    @classmethod
    def search_applications(cls, account, q, page, page_size, sort=None):
        search_subs = app.config.get("DISCOVERY_APPLICATION_SEARCH_SUBS", {})
        sort_subs = app.config.get("DISCOVERY_APPLICATION_SORT_SUBS", {})
        query, page, page_size = cls._make_application_query(account, q, page, page_size, sort, search_subs, sort_subs)

        # execute the query against the articles
        res = models.Suggestion.query(q=query.query(), consistent_order=False)

        # check to see if there was a search error
        if res.get("error") is not None:
            magic = uuid.uuid1()
            app.logger.error("Error executing discovery query search: {x} (ref: {y})".format(x=res.get("error"), y=magic))
            raise DiscoveryException("There was an error executing your query (ref: {y})".format(y=magic))

        obs = [models.Suggestion(**raw) for raw in esprit.raw.unpack_json_result(res)]
        return cls._make_response(res, q, page, page_size, sort, obs)

class SearchQuery(object):
    def __init__(self, qs, fro, psize, sortby=None, sortdir=None):
        self.qs = qs
        self.fro = fro
        self.psize = psize
        self.sortby = sortby
        self.sortdir = sortdir if sortdir is not None else "asc"

    def query(self):
        q = {
            "query" : {
                "filtered" : {
                    "filter" : {
                        "bool" : {
                            "must" : [
                                {"term" : {"admin.in_doaj": True}}
                            ]
                        }
                    },
                    "query" : {
                        "query_string" : {
                            "query" : self.qs
                        }
                    }
                }
            },
            "_source": {
                "include": ["last_updated", "created_date", "id", "bibjson"],
                "exclude": [],
            },
            "from" : self.fro,
            "size" : self.psize
        }

        if self.sortby is not None:
            q["sort"] = [{self.sortby : {"order" : self.sortdir, "mode" : "min"}}]

        return q

class ApplicationQuery(object):
    def __init__(self, owner, qs, fro, psize, sortby=None, sortdir=None):
        self.owner = owner
        self.qs = qs
        self.fro = fro
        self.psize = psize
        self.sortby = sortby
        self.sortdir = sortdir if sortdir is not None else "asc"

    def query(self):
        q = {
            "query" : {
                "filtered" : {
                    "filter" : {
                        "bool" : {
                            "must" : [
                                {"term" : {"admin.owner.exact": self.owner}}
                            ]
                        }
                    },
                    "query" : {
                        "query_string" : {
                            "query" : self.qs
                        }
                    }
                }
            },
            "_source": {
                "include": ["admin.application_status", "suggestion", "last_updated", "created_date", "id", "bibjson"],
                "exclude": [],
            },
            "from" : self.fro,
            "size" : self.psize
        }

        if self.sortby is not None:
            q["sort"] = [{self.sortby : {"order" : self.sortdir, "mode" : "min"}}]

        return q