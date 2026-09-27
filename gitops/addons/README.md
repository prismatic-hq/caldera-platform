# addons

One ApplicationSet per platform addon (REQUIREMENTS.md Section 6), using the Argo CD cluster generator.
Each addon is gated by an `enable_<addon>` label on the cluster Secret and reads AWS metadata
(account, region, role ARNs, zone ID) from its annotations, following the GitOps Bridge contract.
