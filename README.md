# integration-matrix

Matrix integration for the OpenDefence stack, as a Zarf package. It needs the [base](https://github.com/opendefence/base) and [core](https://github.com/opendefence/core) packages in the cluster.

```sh
mise install
task up BASE_VERSION=<version> CORE_VERSION=<version>  # cluster, base, core, then this checkout
task build:deploy                                      # this checkout only
task down
```
