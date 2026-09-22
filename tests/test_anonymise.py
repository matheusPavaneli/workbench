"""The anonymiser, tested as a leak check rather than as a transformer.

`wb ctx record` exists to close the gap between the packaged fixtures and a real
tenant. It writes a file people commit, built from a Jira issue -- one of the
more reliably confidential objects in a company. So the tests below are mostly
adversarial: a payload stuffed with everything that must not survive, asserted
absent from the output.

The second half is the opposite property, and it is what makes the fixture worth
having at all: the *shape* has to come through exactly. A recording whose keys
or nesting drifted would test a payload no tracker ever sends.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from workbench import anonymise, redact  # noqa: E402

SECRETS = {
    "person": "Marina Castilho",
    "email": "marina.castilho@acme-internal.com",
    "host": "https://acme.atlassian.net",
    "prose": "Customer ACME Corp reports the coupon SAVE20 double-charges card 4111111111111111",
    "account": "557058:9d0f8e12-4b3a-4c1e-9f2b-1a2b3c4d5e6f",
    "token": "ATATT3xFfGF0T4Zq8vLmNpQrStUvWxYz012345",
}

PAYLOAD = {
    "id": "10042",
    "key": "ABC-123",
    "self": "https://acme.atlassian.net/rest/api/3/issue/10042",
    "fields": {
        "summary": SECRETS["prose"],
        "description": f"Reported by {SECRETS['person']} ({SECRETS['email']}).\n\n- charge twice\n- refund fails",
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate", "colorName": "yellow"}},
        "issuetype": {"name": "Bug", "subtask": False},
        "assignee": {
            "accountId": SECRETS["account"],
            "displayName": SECRETS["person"],
            "emailAddress": SECRETS["email"],
        },
        "customfield_10042": "Acceptance: refund must be idempotent for ACME Corp",
        "labels": ["billing", "acme-escalation"],
        "comment": {
            "total": 2,
            "comments": [
                {"author": {"displayName": SECRETS["person"]}, "body": "Escalated by ACME's CTO, ping me"},
                {"author": {"displayName": "Other Person"}, "body": "Fixed in staging"},
            ],
        },
    },
}


class NothingIdentifiableSurvives(unittest.TestCase):
    def setUp(self) -> None:
        redact.reset()
        redact.register(SECRETS["token"])
        self.output = json.dumps(anonymise.Anonymiser().payload(PAYLOAD), ensure_ascii=False)

    def test_no_personal_name_survives(self) -> None:
        self.assertNotIn(SECRETS["person"], self.output)

    def test_no_email_survives(self) -> None:
        self.assertNotIn(SECRETS["email"], self.output)
        self.assertNotIn("acme-internal.com", self.output)

    def test_no_customer_name_survives_in_free_text(self) -> None:
        self.assertNotIn("ACME Corp", self.output)
        self.assertNotIn("SAVE20", self.output)

    def test_no_card_number_survives(self) -> None:
        self.assertNotIn("4111111111111111", self.output)

    def test_no_tenant_host_survives(self) -> None:
        self.assertNotIn("acme.atlassian.net", self.output)

    def test_no_account_id_survives(self) -> None:
        self.assertNotIn(SECRETS["account"], self.output)

    def test_no_project_code_survives(self) -> None:
        """A ticket key names the tenant's project, and a recording is made by
        naming one: `wb ctx record SAAS-123` used to write that key, verbatim,
        into a file people commit."""
        written = json.dumps(
            anonymise.Anonymiser().payload(
                {"key": "SAAS-123", "fields": {"description": "blocked by SAAS-120"}}
            )
        )
        self.assertNotIn("SAAS", written)

    def test_a_registered_secret_never_reaches_the_file(self) -> None:
        payload = {"fields": {"description": f"use token {SECRETS['token']} to retry"}}
        written = json.dumps(anonymise.Anonymiser().payload(payload))
        self.assertNotIn(SECRETS["token"], written)

    def test_the_content_of_an_unknown_custom_field_is_replaced(self) -> None:
        """The field this exists to preserve the *shape* of is also the field
        most likely to hold something confidential."""
        self.assertNotIn("idempotent", self.output)
        self.assertNotIn("Acceptance:", self.output)

    def test_two_runs_do_not_agree(self) -> None:
        """A stable mapping across runs would let two fixtures be correlated."""
        first = json.dumps(anonymise.Anonymiser().payload(PAYLOAD))
        second = json.dumps(anonymise.Anonymiser().payload(PAYLOAD))
        self.assertNotEqual(first, second)


class TheShapeSurvives(unittest.TestCase):
    """What makes the recording worth having: the code branches on structure."""

    def setUp(self) -> None:
        self.result = anonymise.Anonymiser().payload(PAYLOAD)

    def test_every_key_is_still_there_at_every_depth(self) -> None:
        self.assertEqual(_shape(PAYLOAD), _shape(self.result))

    def test_structural_values_the_code_branches_on_are_untouched(self) -> None:
        fields = self.result["fields"]
        self.assertEqual("10042", self.result["id"])
        self.assertEqual("Bug", fields["issuetype"]["name"])
        self.assertEqual("In Progress", fields["status"]["name"])
        self.assertEqual("indeterminate", fields["status"]["statusCategory"]["key"])
        self.assertFalse(fields["issuetype"]["subtask"])

    def test_a_key_keeps_its_shape_but_not_the_tenants_project(self) -> None:
        """The project letters name the tenant; the shape is what the providers
        parse. Replacing the whole key would make every key-shaped branch
        untestable, keeping it leaks which company the recording came from."""
        key = self.result["key"]
        self.assertRegex(key, r"^[A-Z][A-Z0-9]{1,9}-123$")
        self.assertNotEqual("ABC-123", key)

    def test_a_structural_value_shaped_like_a_key_is_not_one(self) -> None:
        """The pattern is loose on purpose -- project codes vary -- so it is
        anchored to the field instead. Unanchored it rewrote a status named
        `UTF-8 encoding` and a field holding `CVE-2021-44228`, which is the
        class of value the whole STRUCTURAL set exists to protect."""
        result = anonymise.Anonymiser().payload(
            {"type": "CVE-2021-44228", "fields": {"status": {"name": "UTF-8 encoding"}}}
        )
        self.assertEqual("CVE-2021-44228", result["type"])
        self.assertEqual("UTF-8 encoding", result["fields"]["status"]["name"])

    def test_a_tenant_never_gets_its_own_project_code_back(self) -> None:
        """The pool holds plausible real codes and is assigned by position, so
        the tenant whose code is in it could be handed it back -- a fixture
        that reads as anonymised while naming the company."""
        for code in anonymise._PROJECTS:
            with self.subTest(code=code):
                result = anonymise.Anonymiser().payload({"key": f"{code}-5"})
                self.assertNotEqual(f"{code}-5", result["key"])
                self.assertRegex(result["key"], r"^[A-Z][A-Z0-9]{1,9}-5$")

    def test_two_projects_never_collide_on_one_pseudonym(self) -> None:
        """Skipping a slot to avoid handing a code back must not hand the
        skipped one to the next project instead."""
        codes = [f"{code}-1" for code in anonymise._PROJECTS]
        result = anonymise.Anonymiser().payload({"linked": [{"key": key} for key in codes]})
        mapped = [entry["key"] for entry in result["linked"]]
        self.assertEqual(len(codes), len(set(mapped)))

    def test_the_same_key_maps_the_same_way_everywhere_in_one_payload(self) -> None:
        """A fixture where the same ticket reads as two different tickets is
        incoherent in a way that is hard to notice and easy to reason wrongly
        from -- the same reason people are mapped consistently."""
        result = anonymise.Anonymiser().payload(
            {
                "key": "ABC-123",
                "self": "https://acme.atlassian.net/browse/ABC-123",
                "fields": {"parent": {"key": "ABC-123"}},
            }
        )
        self.assertEqual(result["key"], result["fields"]["parent"]["key"])
        self.assertTrue(result["self"].endswith(result["key"]), result["self"])

    def test_list_lengths_are_preserved(self) -> None:
        self.assertEqual(2, len(self.result["fields"]["comment"]["comments"]))
        self.assertEqual(2, len(self.result["fields"]["labels"]))

    def test_numbers_and_booleans_are_left_alone(self) -> None:
        self.assertEqual(2, self.result["fields"]["comment"]["total"])

    def test_prose_keeps_its_line_and_list_structure(self) -> None:
        """The depth caps and summarisers in this package are length-sensitive:
        text collapsed to one word would exercise a path no payload takes."""
        original = PAYLOAD["fields"]["description"]
        replaced = self.result["fields"]["description"]
        self.assertEqual(len(original.splitlines()), len(replaced.splitlines()))
        self.assertEqual(
            sum(1 for line in original.splitlines() if line.startswith("- ")),
            sum(1 for line in replaced.splitlines() if line.startswith("- ")),
        )

    def test_a_url_keeps_its_path_shape(self) -> None:
        """Providers branch on the path; the host is what identifies the tenant."""
        self.assertIn("/rest/api/3/issue/", self.result["self"])
        self.assertNotIn("acme", self.result["self"])

    def test_one_person_stays_one_person(self) -> None:
        """A fixture where the same author reads as two people is incoherent."""
        result = anonymise.Anonymiser().payload(PAYLOAD)
        assignee = result["fields"]["assignee"]["displayName"]
        commenter = result["fields"]["comment"]["comments"][0]["author"]["displayName"]
        self.assertEqual(assignee, commenter)

    def test_two_different_people_stay_two_people(self) -> None:
        """Regression, and it was intermittent: hashing into a pool of six names
        collided about one run in six, so two commenters could read as one
        author -- incoherent in a way that is easy to reason wrongly from."""
        result = anonymise.Anonymiser().payload(PAYLOAD)
        comments = result["fields"]["comment"]["comments"]
        self.assertNotEqual(comments[0]["author"]["displayName"], comments[1]["author"]["displayName"])

    def test_no_two_people_ever_collide_however_many_there_are(self) -> None:
        """The property the pool cannot provide by itself: more distinct people
        than names must still produce distinct names."""
        anonymiser = anonymise.Anonymiser()
        people = [f"Person Number {index}" for index in range(25)]
        pseudonyms = [anonymiser.person(name) for name in people]
        self.assertEqual(len(people), len(set(pseudonyms)))

    def test_the_same_person_is_still_one_person_across_a_payload(self) -> None:
        anonymiser = anonymise.Anonymiser()
        first = anonymiser.person("Marina Castilho")
        anonymiser.person("Somebody Else")
        self.assertEqual(first, anonymiser.person("Marina Castilho"))


def _shape(data, key=""):
    """The structure alone: keys, nesting, types, lengths -- no values."""
    if isinstance(data, dict):
        return {name: _shape(value, name) for name, value in sorted(data.items())}
    if isinstance(data, list):
        return [_shape(item, key) for item in data]
    return type(data).__name__


if __name__ == "__main__":
    unittest.main()
