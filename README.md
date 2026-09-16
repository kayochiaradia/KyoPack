# KyoPack

**KyoPack** é um forjador automático de pacotes de custom firmware para
Nintendo Switch. Em vez de empacotar tudo manualmente toda vez que um
componente lança uma versão nova, o KyoPack consulta as *releases* oficiais
de cada projeto upstream (Atmosphère, hekate, e o que você adicionar em
[`components.yaml`](./components.yaml)), monta a estrutura de SD card e
publica um `.zip` pronto — tudo sozinho, num cron do GitHub Actions.

Se um componente não teve versão nova desde a última execução, nada é
publicado. Se algo mudou, sai uma release nova com changelog automático.

## Como funciona

```
components.yaml  ──►  scripts/build_pack.py  ──►  build/  ──►  dist/KyoPack-<versão>.zip
       │                       │
       │                       └─ compara com state/versions.json
       │                          e só gera pacote se algo mudou
       │
       └─ lista de "owner/repo" + regex do asset certo + onde colocar
```

O workflow [`build-pack.yml`](./.github/workflows/build-pack.yml) roda
diariamente (e também pode ser disparado manualmente na aba *Actions*),
executa o builder e, se houver atualização:

1. commita o novo `state/versions.json`;
2. cria uma *release* no GitHub com o zip anexado e um changelog no formato:

   ```
   [Changelog]

   Atualizações do Pacote.

   - Atmosphère 1.11.2.
   - hekate 6.5.3.

   [Problemas Conhecidos]

   Nenhum problema conhecido no momento.
   Encontrou algo? Abra uma issue no repositório do KyoPack.
   ```

## Rodando localmente

```bash
pip install -r scripts/requirements.txt
python scripts/build_pack.py          # só gera pacote se algo mudou
python scripts/build_pack.py --force  # força a geração mesmo sem mudanças
```

Um `GITHUB_TOKEN` no ambiente é opcional, mas evita bater no limite de
requisições anônimas da API do GitHub.

## Adicionando um componente

Edite `components.yaml`. **Confirme o repositório real e o nome do asset na
aba Releases antes de adicionar** — o builder busca literalmente a última
release do `repo` informado, então um slug errado baixa a coisa errada.

```yaml
- name: NomeDoComponente
  repo: dono/repositorio
  asset_regex: '^padrao-do-asset\.zip$'
  place: zip-merge   # ou zip-to / copy-to
  dest: caminho/dentro/do/pacote   # só para zip-to e copy-to
  strip_v: false     # true remove um "v" inicial da tag (v1.2.3 -> 1.2.3)
```

## Aviso legal

O KyoPack **não contém e não distribui nenhum arquivo de jogo, backup de
cartucho ou conteúdo protegido por direitos autorais**. Ele apenas
automatiza o download de *releases públicas* de projetos de código aberto
que já são distribuídos livremente em seus próprios repositórios.

O propósito do custom firmware empacotado aqui é habilitar a execução de
homebrews — aplicativos independentes feitos pela comunidade. O uso desse
software para rodar cópias não autorizadas de jogos é proibido, total ou
parcialmente, pela legislação da maioria dos países, e é de inteira
responsabilidade de quem o utiliza verificar e respeitar as leis da sua
própria jurisdição.

O pacote inclui instaladores de título (DBI, Sphaira) usados para gerenciar
backups dos seus próprios jogos — dumps feitos por você mesmo do seu próprio
cartucho ou compra digital. Instalar arquivos de jogos que você não possui é
pirataria e não é o uso pretendido nem suportado por este projeto.

Nintendo Switch é uma marca registrada da Nintendo. Este projeto não é
afiliado, patrocinado ou endossado pela Nintendo.

## Licença

O código deste repositório (scripts de build, workflow, configuração) está
sob licença MIT — veja [`LICENSE`](./LICENSE). Cada componente baixado pelo
KyoPack mantém sua própria licença original, definida pelo respectivo
projeto upstream.
