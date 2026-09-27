# Cluster Access

The EKS API endpoint is private, so `kubectl` reaches it through an SSM port-forwarding tunnel
to a system node. Access works in any AWS account and does not depend on SSO permission sets.

## Who can connect

`CalderaCluster` creates `ClusterAdminRole` (stack output `ClusterAdminRoleArn`) with the
`AmazonEKSClusterAdminPolicy` access entry at cluster scope.

- Default: the role trusts the account root, so any IAM principal in the account whose own
  policy allows `sts:AssumeRole` on the role can use it.
- Override: set `clusterAdminPrincipals` (IAM role or user ARNs) in
  `deploy/environments/<name>.yaml` or with `-c clusterAdminPrincipals=arn1,arn2`, then redeploy
  `CalderaCluster`. Only those principals are trusted.

## Required permissions

The principal running `mise run kube:connect` needs:

- `sts:AssumeRole` on `ClusterAdminRole`
- `ssm:StartSession` on the system node instances and on the
  `AWS-StartPortForwardingSessionToRemoteHost` document
- `cloudformation:DescribeStacks` on `CalderaCluster`, `eks:DescribeCluster`,
  `eks:ListNodegroups`, `eks:DescribeNodegroup` and `ec2:DescribeInstances`

## Prerequisites

- AWS CLI v2 with credentials and a region for the account (`AWS_REGION` or `--region`)
- Session Manager plugin: `brew install --cask session-manager-plugin`

## Connect

```sh
mise run kube:connect                      # leave running; Ctrl-C closes the tunnel
kubectl --context caldera get nodes    # in a second terminal
```

`mise run kube:connect -- --port 9443 --cluster caldera --stack CalderaCluster` overrides the
defaults. Each run picks the newest running system node and rewrites only the `caldera` entries
in your kubeconfig (`$KUBECONFIG` or `~/.kube/config`). Tokens come from
`aws eks get-token --role-arn <ClusterAdminRoleArn>`, so kubectl assumes the role on each call.
