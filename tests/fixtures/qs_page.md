---
title: Quickstart
---

## 1. Get a key

```sh {test="true"}
mkdir -p demo && cd demo
echo "QRYPT_API_KEY=paste-your-key-here" > .env
```

A block that is shown but never run:

```sh
echo "not tested"
```

{{< qs-tabs id="get" >}}
{{% qs-tab name="Node.js" %}}
```sh {test="true"}
curl -fsSLO https://qrypt-quickstart.pages.dev/quickstart/files/quickstart.mjs
```
{{% /qs-tab %}}
{{% qs-tab name="Python" %}}

```bash {test="true" linenos=false}
curl -fsSLO https://qrypt-quickstart.pages.dev/quickstart/files/quickstart.py
```

```sh {test="true"}
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```
{{% /qs-tab %}}
{{< /qs-tabs >}}

~~~sh {test='true'}
echo "tilde fence, single quotes"
~~~

  ```text {test="true"}
  indented by two spaces
    keeps relative indent
  ```

````md {test="true"}
```inner fence is content
```
````

{{< qs-tabs id="run" >}}
{{% qs-tab name="Node.js" %}}
```sh {test="true"}
node quickstart.mjs
```
{{% /qs-tab %}}
{{% qs-tab name="Python" %}}
```sh {test="true"}
python quickstart.py
```
{{% /qs-tab %}}
{{< /qs-tabs >}}

## Windows

```powershell {test="pwsh"}
Set-Content -Path .env -Value "QRYPT_API_KEY=paste-your-key-here"
```
