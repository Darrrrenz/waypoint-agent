# Deterministic benchmark

Passed: 32/32; storage: memory; repeats: 2.

Synthetic harness approvals; no live inference. Unknown usage/cost: N/A.

| Scenario | Repeat | Result | Runtime | Calls | Mutations | Failures |
| --- | --- | --- | --- | --- | --- | --- |
| retrieval | 0 | passed | completed | 4 | 0 |  |
| exclude_read_unrelated | 0 | passed | completed | 4 | 0 |  |
| missing_meeting | 0 | passed | completed | 2 | 0 |  |
| ambiguous_meetings | 0 | passed | completed | 2 | 0 |  |
| no_related_unread | 0 | passed | completed | 3 | 0 |  |
| approved_reschedule | 0 | passed | completed | 5 | 1 |  |
| transient_read | 0 | passed | completed | 5 | 1 |  |
| commit_recovery | 0 | passed | completed | 5 | 1 |  |
| no_destination_slot | 0 | passed | completed | 3 | 0 |  |
| approval_denied | 0 | passed | denied | 3 | 0 |  |
| occupied_after_approval | 0 | passed | completed | 8 | 2 |  |
| malformed_model | 0 | passed | failed | 3 | 0 |  |
| memory_disabled | 0 | passed | completed | 9 | 1 |  |
| memory_structured | 0 | passed | completed | 9 | 1 |  |
| memory_changed | 0 | passed | completed | 5 | 1 |  |
| memory_no_slot | 0 | passed | completed | 3 | 0 |  |
| retrieval | 1 | passed | completed | 4 | 0 |  |
| exclude_read_unrelated | 1 | passed | completed | 4 | 0 |  |
| missing_meeting | 1 | passed | completed | 2 | 0 |  |
| ambiguous_meetings | 1 | passed | completed | 2 | 0 |  |
| no_related_unread | 1 | passed | completed | 3 | 0 |  |
| approved_reschedule | 1 | passed | completed | 5 | 1 |  |
| transient_read | 1 | passed | completed | 5 | 1 |  |
| commit_recovery | 1 | passed | completed | 5 | 1 |  |
| no_destination_slot | 1 | passed | completed | 3 | 0 |  |
| approval_denied | 1 | passed | denied | 3 | 0 |  |
| occupied_after_approval | 1 | passed | completed | 8 | 2 |  |
| malformed_model | 1 | passed | failed | 3 | 0 |  |
| memory_disabled | 1 | passed | completed | 9 | 1 |  |
| memory_structured | 1 | passed | completed | 9 | 1 |  |
| memory_changed | 1 | passed | completed | 5 | 1 |  |
| memory_no_slot | 1 | passed | completed | 3 | 0 |  |

| Scenario / repeat | Proposals | Attempts | Retries | Invalid | Active s | Wall s |
| --- | --- | --- | --- | --- | --- | --- |
| retrieval / 0 | 3 | 3 | 0 | 0 | 0.0096 | 0.0114 |
| exclude_read_unrelated / 0 | 3 | 3 | 0 | 0 | 0.0073 | 0.0087 |
| missing_meeting / 0 | 1 | 1 | 0 | 0 | 0.0059 | 0.0069 |
| ambiguous_meetings / 0 | 1 | 1 | 0 | 0 | 0.0036 | 0.0047 |
| no_related_unread / 0 | 2 | 2 | 0 | 0 | 0.0054 | 0.0067 |
| approved_reschedule / 0 | 4 | 4 | 0 | 0 | 0.0164 | 0.0194 |
| transient_read / 0 | 4 | 5 | 1 | 0 | 0.0714 | 0.0738 |
| commit_recovery / 0 | 4 | 4 | 0 | 0 | 0.0172 | 0.0196 |
| no_destination_slot / 0 | 2 | 2 | 0 | 0 | 0.0089 | 0.0102 |
| approval_denied / 0 | 3 | 2 | 0 | 0 | 0.0090 | 0.0107 |
| occupied_after_approval / 0 | 7 | 7 | 0 | 0 | 0.0277 | 0.0313 |
| malformed_model / 0 | 0 | 0 | 0 | 3 | 0.0053 | 0.0063 |
| memory_disabled / 0 | 7 | 7 | 0 | 0 | 0.0231 | 0.0262 |
| memory_structured / 0 | 7 | 7 | 0 | 0 | 0.0276 | 0.0314 |
| memory_changed / 0 | 4 | 4 | 0 | 0 | 0.0170 | 0.0204 |
| memory_no_slot / 0 | 2 | 2 | 0 | 0 | 0.0106 | 0.0122 |
| retrieval / 1 | 3 | 3 | 0 | 0 | 0.0080 | 0.0093 |
| exclude_read_unrelated / 1 | 3 | 3 | 0 | 0 | 0.0077 | 0.0091 |
| missing_meeting / 1 | 1 | 1 | 0 | 0 | 0.0038 | 0.0052 |
| ambiguous_meetings / 1 | 1 | 1 | 0 | 0 | 0.0043 | 0.0059 |
| no_related_unread / 1 | 2 | 2 | 0 | 0 | 0.0055 | 0.0068 |
| approved_reschedule / 1 | 4 | 4 | 0 | 0 | 0.0165 | 0.0189 |
| transient_read / 1 | 4 | 5 | 1 | 0 | 0.0715 | 0.0737 |
| commit_recovery / 1 | 4 | 4 | 0 | 0 | 0.0170 | 0.0190 |
| no_destination_slot / 1 | 2 | 2 | 0 | 0 | 0.0082 | 0.0100 |
| approval_denied / 1 | 3 | 2 | 0 | 0 | 0.0089 | 0.0106 |
| occupied_after_approval / 1 | 7 | 7 | 0 | 0 | 0.0281 | 0.0320 |
| malformed_model / 1 | 0 | 0 | 0 | 3 | 0.0072 | 0.0091 |
| memory_disabled / 1 | 7 | 7 | 0 | 0 | 0.0266 | 0.0310 |
| memory_structured / 1 | 7 | 7 | 0 | 0 | 0.0251 | 0.0289 |
| memory_changed / 1 | 4 | 4 | 0 | 0 | 0.0170 | 0.0197 |
| memory_no_slot / 1 | 2 | 2 | 0 | 0 | 0.0091 | 0.0111 |

| Scenario / repeat | Tool choices | Arguments | Recovery | Preference | Unnecessary | Disallowed | Usage | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| retrieval / 0 | 3/3 | 1/1 | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| exclude_read_unrelated / 0 | 3/3 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| missing_meeting / 0 | 1/1 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| ambiguous_meetings / 0 | 1/1 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| no_related_unread / 0 | 2/2 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| approved_reschedule / 0 | 4/4 | N/A (0) | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| transient_read / 0 | 4/4 | N/A (0) | 1/1 | N/A (0) | 0 | 0 | N/A | N/A |
| commit_recovery / 0 | 4/4 | N/A (0) | 1/1 | N/A (0) | 0 | 0 | N/A | N/A |
| no_destination_slot / 0 | 2/2 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| approval_denied / 0 | 3/3 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| occupied_after_approval / 0 | 7/7 | N/A (0) | 1/1 | N/A (0) | N/A | 0 | N/A | N/A |
| malformed_model / 0 | N/A (0) | N/A (0) | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| memory_disabled / 0 | 7/7 | 1/1 | N/A (0) | 0/1 | 0 | 0 | N/A | N/A |
| memory_structured / 0 | 7/7 | 1/1 | N/A (0) | 1/1 | 0 | 0 | N/A | N/A |
| memory_changed / 0 | 4/4 | N/A (0) | N/A (0) | 1/1 | 0 | 0 | N/A | N/A |
| memory_no_slot / 0 | 2/2 | N/A (0) | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| retrieval / 1 | 3/3 | 1/1 | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| exclude_read_unrelated / 1 | 3/3 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| missing_meeting / 1 | 1/1 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| ambiguous_meetings / 1 | 1/1 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| no_related_unread / 1 | 2/2 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| approved_reschedule / 1 | 4/4 | N/A (0) | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| transient_read / 1 | 4/4 | N/A (0) | 1/1 | N/A (0) | 0 | 0 | N/A | N/A |
| commit_recovery / 1 | 4/4 | N/A (0) | 1/1 | N/A (0) | 0 | 0 | N/A | N/A |
| no_destination_slot / 1 | 2/2 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| approval_denied / 1 | 3/3 | N/A (0) | N/A (0) | N/A (0) | N/A | 0 | N/A | N/A |
| occupied_after_approval / 1 | 7/7 | N/A (0) | 1/1 | N/A (0) | N/A | 0 | N/A | N/A |
| malformed_model / 1 | N/A (0) | N/A (0) | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |
| memory_disabled / 1 | 7/7 | 1/1 | N/A (0) | 0/1 | 0 | 0 | N/A | N/A |
| memory_structured / 1 | 7/7 | 1/1 | N/A (0) | 1/1 | 0 | 0 | N/A | N/A |
| memory_changed / 1 | 4/4 | N/A (0) | N/A (0) | 1/1 | 0 | 0 | N/A | N/A |
| memory_no_slot / 1 | 2/2 | N/A (0) | N/A (0) | N/A (0) | 0 | 0 | N/A | N/A |

Mutations include the explicitly labeled competing harness actor. Preference satisfaction is separate from scenario success.

Full assertions, denominators, hashes, execution limits, timing and usage coverage are in report.json. Per-run files contain trajectories and world snapshots.
