allow_k8s_contexts(os.getenv('KUBE_CONTEXT', 'kind-opendefence-dev'))

# Zarf deploys all but the operator: the chart's hooks need Helm
operator_files = ['manifests/operator/rbac.yaml', 'manifests/operator/deployment.yaml']
local_resource(
    'package',
    'zarf dev deploy . --connected --values values/local-dev.yaml --components=-operator',
    deps=['zarf.yaml', 'manifests', 'values', 'helm-values'],
    ignore=operator_files,
)

operator = [obj for path in operator_files for obj in read_yaml_stream(path)]
k8s_yaml(encode_yaml_stream(operator))
docker_build('ghcr.io/opendefence/integration-matrix-operator', '.', only=['src', 'pyproject.toml', 'uv.lock'])
k8s_resource(
    'matrix-operator',
    # With the Deployment: the package makes the namespace
    objects=['%s:%s' % (obj['metadata']['name'], obj['kind'].lower()) for obj in operator if obj['kind'] != 'Deployment'],
    resource_deps=['package'],
)
