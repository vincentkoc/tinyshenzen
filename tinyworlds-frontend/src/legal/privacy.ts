import type { LegalDocs } from "./types";

/**
 * The privacy policy, in the four languages, section by section and point by point with the
 * same links (`legal.test.ts` checks). It says what the code does: the funnel and the visits
 * (`lib/funnel.ts`, the API's `funnel/` and `visits/`), the quiz's answers, the two kinds of
 * analytics (`components/analytics.tsx`, `lib/consent.ts`), what the browser keeps, the
 * donations and donors (`tinyworlds-api/prisma/schema.prisma`), the links, the sign-in and the
 * lists people ask to join (`lib/lists.ts`, the API's `lists/`).
 * Change it with them.
 */
export const PRIVACY: LegalDocs = {
  "en-US": {
    title: "Privacy Policy",
    description:
      "What tinyworlds collects when you explore, donate or sign in, why, who else sees it, and how to have it changed or deleted.",
    summary: [
      "You can explore every map without telling us who you are. We sell no data and show no ads.",
      "Visits are counted with a random id that lives in one browser tab, not with cookies.",
      "Google Analytics' cookies are used only where allowed: in Europe we ask first, and anyone can say no.",
      "If you donate, Stripe handles the payment; we keep your email, what you gave and, if you choose, your name on a city.",
      "If you ask to be told when a city is built, we keep your email on that list and send only its mails; each one has a link to leave.",
      "Write to {email} to see, correct or delete what we hold about you.",
    ],
    sections: [
      {
        id: "who",
        title: "Who we are",
        blocks: [
          "tinyworlds is a small independent project, made and run by one person. We decide what is collected on this site and why, and you can reach us at {email}. This policy covers the whole site: the front page, every map, Your cities and the Hall of Fame.",
        ],
      },
      {
        id: "exploring",
        title: "When you explore",
        blocks: [
          "Exploring needs no account, and the maps never ask who you are. So that we know which maps are visited, and how a donation came about, each page tells our server:",
          {
            items: [
              {
                term: "That it was opened:",
                text: "which map, and the interface's language, under a random id that the page makes up and keeps in that tab's session storage. The id is gone when the tab closes; it is not a cookie, and it says nothing about who you are.",
              },
              {
                term: "That a map is still open:",
                text: "every 30 seconds while it is in view, so that the site can say how many people are exploring now, and we can see how long visits last.",
              },
              {
                term: "The steps towards a donation:",
                text: "opening the donate sign, starting a checkout, or coming back from one without paying, with the city and the amount chosen.",
              },
              {
                term: "Your answers in Guess the building:",
                text: "the building asked about, the name you picked, the difficulty and the language, so that everyone can see how others answered.",
              },
            ],
          },
          "None of this holds your name, your email or your IP address. If you then donate, the donation keeps the visit's id, which ties that visit's steps to your gift. Visits from a browser where one of our admins is signed in, or from a browser driven by a program, are not counted.",
        ],
      },
      {
        id: "analytics",
        title: "Analytics and cookies",
        blocks: [
          "We use two analytics tools to learn how people find and use the maps:",
          {
            items: [
              {
                term: "Rybbit,",
                text: "which we run ourselves. It counts page views and a few clicks (opening the donate sign, starting the game), with the page, the site you came from, your browser, device and language, and the country and region worked out from your IP address. It sets no cookies.",
              },
              {
                term: "Google Analytics,",
                text: "through Google Tag Manager. It does set cookies (_ga), and Google processes what it collects under its own [privacy policy](google). If your browser is set to a European time zone (or hides its time zone), we ask first, and nothing from Google loads until you say yes; elsewhere it loads with the page, with everything to do with advertising turned off. Wherever you are, you can say no at any time in [Cookie settings](cookies), on the front page or in a map's About: a no is remembered, and Google's cookies are deleted.",
              },
            ],
          },
          "No page of the site uses cookies for advertising.",
        ],
      },
      {
        id: "your-browser",
        title: "What stays in your browser",
        blocks: [
          "The site keeps a few things in your browser's storage, which never leave it except as described above:",
          {
            items: [
              "your settings (language, theme, the map's switches) and your answer to the cookie question;",
              "the buildings you have already played in Guess the building, and the game you chose;",
              "the visit's id, and what you chose in the donate sign while you are at the checkout (in session storage, gone when the tab closes);",
              "if you sign in, your session, for 7 days;",
              'if you press "Tell me" before signing in, which list you asked for and when (under tinyworlds.join): it is read and removed once the sign-in completes, and ignored if it is more than an hour old.',
            ],
          },
          "You can clear them at any time in your browser's settings.",
        ],
      },
      {
        id: "donating",
        title: "When you donate",
        blocks: [
          "Payments are handled by Stripe, under its [privacy policy](stripe). Stripe collects your card or other payment details and your email address; we never see your card details. From Stripe we receive and keep:",
          {
            items: [
              "your email address;",
              "the amount, the currency, the date and Stripe's references for the payment;",
              "the city you named, the map you came from, the language and the visit's id;",
              "the name you asked to be listed under, if you did.",
            ],
          },
          "We use your email address to thank you for your gift, to tell you when a city you gave to is built, and to tell you, once, that you are in the Hall of Fame; the person who makes tinyworlds may also write to you once, from their own address, to thank you personally. We send no newsletters or marketing beyond the lists you ask to join (below), and your email address is never shown on the site, to anyone.",
        ],
      },
      {
        id: "public",
        title: "What is public",
        blocks: [
          "A gift's amount and stones are shown on its city's list without saying who gave them, unless you ask to be named: then your name is shown there, on the front page and in the Hall of Fame, with the links you add. You can stop being named, change your name or remove your links at any time on [Your cities](me). We may hide a name or links that break our [Terms of Service](terms).",
          "To show a link, our server reads the linked page once (and again whenever you ask) and keeps its title, its description and small copies of its icon and picture, so that visitors' browsers never contact the linked site.",
        ],
      },
      {
        id: "signing-in",
        title: "When you sign in",
        blocks: [
          'Anyone may sign in with an email address, to see what they have given or to join a list (below). We keep the address and when it last signed in, whether or not it has given, until you ask us to delete it. Each sign-in link and code works once, for 15 minutes; we store only a one-way hash of them, never the link or the code itself, and, with a link asked for from "Tell me", the list it is for.',
        ],
      },
      {
        id: "lists",
        title: "When you ask to be told",
        blocks: [
          'Anyone may ask to hear when a city is built, or when new cities are built, by pressing "Tell me" and confirming their email address with a sign-in link or code. For each list you join we keep, beside your address, which list it is, when you joined and the language of the page you joined from. We send:',
          {
            items: [
              "one welcome mail, with three buildings from the cities already on the map;",
              'one mail when a city you are waiting for is built; for "new cities", one whenever a new world is added to the map.',
            ],
          },
          'Nothing else is sent through the lists. A city\'s list ends once its mail has gone out; "new cities" lasts until you leave it.',
          'You can leave at any time: every list mail has a "Leave this list" link that needs no sign-in, and your mail program\'s own unsubscribe button works too. You can also leave any list on [Your cities](me).',
          "Leaving takes you off the list, and nothing more: your address stays, as for any sign-in (above), with a note of the list mails sent to it (when the welcome went, and which city each of the others was about, and when), so that nobody is told twice. Both stay until you ask us to delete them.",
        ],
      },
      {
        id: "sharing",
        title: "Who else sees it",
        blocks: [
          "We do not sell or rent personal data, and we do not share it for advertising. Besides us, it reaches only:",
          {
            items: [
              { term: "Stripe,", text: "for payments." },
              { term: "Google,", text: "for Google Analytics, where it is allowed (above)." },
              {
                term: "The servers the site runs on,",
                text: "for the site, its database, its mail and Rybbit. Like any web server, they see your IP address and browser and write them in technical logs, used only to run and protect the site; the API also uses addresses for a moment, to limit how often anything can be asked of it.",
              },
              { term: "The authorities,", text: "if the law requires it." },
            ],
          },
          "Stripe and Google may process data outside your country, including in the United States.",
        ],
      },
      {
        id: "keeping",
        title: "How long we keep it",
        blocks: [
          {
            items: [
              {
                term: "Donations:",
                text: "for as long as the site runs: they make up each city's funding and its list of benefactors, and they are the record of payments.",
              },
              {
                term: "Your address and donor details",
                text: "(email address, name, links), whether you gave or only signed in: until you ask us to delete them.",
              },
              {
                term: "Visits and quiz answers:",
                text: "kept, with nothing in them that points to you, to count visits and answers over time.",
              },
              {
                term: "List memberships:",
                text: "until you leave the list or, for a city, until its mail has gone out.",
              },
              {
                term: "Notes of list mails",
                text: "(which city each was about and when, and when the welcome went): until you ask us to delete your address, so that nobody is told twice.",
              },
              {
                term: "Sign-in links and codes:",
                text: "only as one-way hashes, with the list a link was asked for, if any: for a day or until that address next asks for one, whichever is later.",
              },
            ],
          },
        ],
      },
      {
        id: "rights",
        title: "Your rights",
        blocks: [
          "Depending on where you live (the GDPR in Europe and the UK, the LGPD in Brazil, the CCPA in California, among others), you may have the right to know what we hold about you, to receive a copy, to have it corrected or deleted, and to object to its use. Wherever you live, write to {email} from the address in question and we will do it within 30 days. If we delete your donor details, your gifts stay counted in their cities, with nothing left that ties them to you. You may also complain to your data protection authority.",
          "Where the law asks for a legal basis: running the site and counting its visits are in our legitimate interest; recording a donation and writing to you about it are needed for the gift you made; Google Analytics' cookies, and showing your name and links, rest on your consent, which you can withdraw at any time.",
        ],
      },
      {
        id: "children",
        title: "Children",
        blocks: [
          "The maps are for everyone. Donating and signing in are for adults: if you are under 18, ask a parent or guardian first. We do not knowingly collect personal information from children under 13; if you think we have, write to us and we will delete it.",
        ],
      },
      {
        id: "changes",
        title: "Changes",
        blocks: [
          "When this policy changes, we update the date at the top, and if a change matters to donors we tell them by email. It is written in English and translated into Portuguese, Spanish and Simplified Chinese; if a translation ever differs from the English text, the English text prevails.",
        ],
      },
    ],
  },

  "pt-BR": {
    title: "Política de Privacidade",
    description:
      "O que o tinyworlds coleta quando você explora, doa ou entra, por quê, quem mais vê esses dados e como pedir que sejam corrigidos ou apagados.",
    summary: [
      "Você explora todos os mapas sem nos dizer quem é. Não vendemos dados nem mostramos anúncios.",
      "As visitas são contadas com um código aleatório que vive numa aba do navegador, não com cookies.",
      "Os cookies do Google Analytics só são usados onde é permitido: na Europa perguntamos antes, e qualquer pessoa pode recusar.",
      "Se você doar, o Stripe cuida do pagamento; guardamos seu e-mail, o que você doou e, se quiser, seu nome numa cidade.",
      "Se você pedir para ser avisado quando uma cidade for construída, guardamos seu e-mail nessa lista e enviamos só os e-mails dela; todos têm um link para sair.",
      "Escreva para {email} para ver, corrigir ou apagar o que temos sobre você.",
    ],
    sections: [
      {
        id: "who",
        title: "Quem somos",
        blocks: [
          "O tinyworlds é um pequeno projeto independente, feito e mantido por uma pessoa. Nós decidimos o que é coletado neste site e para quê, e você nos encontra em {email}. Esta política vale para o site inteiro: a página inicial, todos os mapas, Suas cidades e o Hall da Fama.",
        ],
      },
      {
        id: "exploring",
        title: "Quando você explora",
        blocks: [
          "Explorar não pede conta, e os mapas nunca perguntam quem você é. Para sabermos quais mapas são visitados, e como uma doação aconteceu, cada página informa ao nosso servidor:",
          {
            items: [
              {
                term: "Que foi aberta:",
                text: "qual mapa e o idioma da interface, com um código aleatório que a página cria e guarda no armazenamento de sessão daquela aba. O código some quando a aba é fechada; não é um cookie e não diz nada sobre quem você é.",
              },
              {
                term: "Que um mapa continua aberto:",
                text: "a cada 30 segundos enquanto está à vista, para o site dizer quantas pessoas estão explorando agora e podermos ver quanto duram as visitas.",
              },
              {
                term: "Os passos rumo a uma doação:",
                text: "abrir o painel de doação, começar um pagamento ou voltar dele sem pagar, com a cidade e o valor escolhidos.",
              },
              {
                term: "Suas respostas no Adivinhe o prédio:",
                text: "o prédio perguntado, o nome que você escolheu, a dificuldade e o idioma, para todos verem como os outros responderam.",
              },
            ],
          },
          "Nada disso contém seu nome, seu e-mail ou seu endereço IP. Se depois você doar, a doação guarda o código da visita, o que liga os passos daquela visita à sua doação. Não contamos visitas de um navegador em que um dos nossos administradores entrou, nem de um navegador controlado por um programa.",
        ],
      },
      {
        id: "analytics",
        title: "Estatísticas e cookies",
        blocks: [
          "Usamos duas ferramentas de estatísticas para entender como as pessoas encontram e usam os mapas:",
          {
            items: [
              {
                term: "Rybbit,",
                text: "que nós mesmos mantemos. Ele conta visualizações de página e alguns cliques (abrir o painel de doação, começar o jogo), com a página, o site de onde você veio, seu navegador, aparelho e idioma, e o país e a região deduzidos do seu endereço IP. Ele não usa cookies.",
              },
              {
                term: "Google Analytics,",
                text: "pelo Google Tag Manager. Ele usa cookies (_ga), e o Google trata o que coleta segundo a própria [política de privacidade](google). Se o seu navegador estiver num fuso horário da Europa (ou esconder o fuso), perguntamos antes, e nada do Google carrega até você aceitar; nos outros lugares ele carrega com a página, com tudo o que tem a ver com publicidade desligado. Esteja onde estiver, você pode recusar a qualquer momento em [Configurações de cookies](cookies), na página inicial ou no Sobre de um mapa: a recusa fica guardada, e os cookies do Google são apagados.",
              },
            ],
          },
          "Nenhuma página do site usa cookies para publicidade.",
        ],
      },
      {
        id: "your-browser",
        title: "O que fica no seu navegador",
        blocks: [
          "O site guarda algumas coisas no armazenamento do seu navegador, que só saem dele como descrito acima:",
          {
            items: [
              "suas configurações (idioma, tema, as opções do mapa) e sua resposta sobre os cookies;",
              "os prédios que você já jogou no Adivinhe o prédio, e o jogo que escolheu;",
              "o código da visita, e o que você escolheu no painel de doação enquanto está no pagamento (no armazenamento de sessão, que some quando a aba é fechada);",
              "se você entrar, sua sessão, por 7 dias;",
              'se você clicar em "Me avise" antes de entrar, qual lista você pediu e quando (em tinyworlds.join): ele é lido e apagado quando o acesso é concluído, e ignorado se tiver mais de uma hora.',
            ],
          },
          "Você pode apagá-las a qualquer momento nas configurações do navegador.",
        ],
      },
      {
        id: "donating",
        title: "Quando você doa",
        blocks: [
          "Os pagamentos são feitos pelo Stripe, segundo a [política de privacidade](stripe) dele. O Stripe coleta os dados do seu cartão ou de outro meio de pagamento e seu e-mail; nós nunca vemos os dados do seu cartão. Do Stripe recebemos e guardamos:",
          {
            items: [
              "seu endereço de e-mail;",
              "o valor, a moeda, a data e as referências do Stripe para o pagamento;",
              "a cidade que você indicou, o mapa de onde veio, o idioma e o código da visita;",
              "o nome com que você pediu para aparecer, se pediu.",
            ],
          },
          "Usamos seu e-mail para agradecer pela doação, para avisar quando uma cidade para a qual você doou for construída e para avisar, uma vez, que você está no Hall da Fama; a pessoa que faz o tinyworlds também pode escrever a você uma vez, do próprio endereço, para agradecer pessoalmente. Não enviamos newsletters nem marketing além das listas em que você pede para entrar (abaixo), e seu e-mail nunca aparece no site, para ninguém.",
        ],
      },
      {
        id: "public",
        title: "O que é público",
        blocks: [
          "O valor e as pedras de uma doação aparecem na lista da cidade sem dizer quem doou, a menos que você peça para ter seu nome: aí seu nome aparece ali, na página inicial e no Hall da Fama, com os links que você adicionar. Você pode deixar de aparecer, mudar seu nome ou remover seus links a qualquer momento em [Suas cidades](me). Podemos ocultar um nome ou links que desrespeitem nossos [Termos de Uso](terms).",
          "Para mostrar um link, nosso servidor lê a página indicada uma vez (e de novo sempre que você pedir) e guarda o título, a descrição e pequenas cópias do ícone e da imagem dela, para que os navegadores dos visitantes nunca contatem o site indicado.",
        ],
      },
      {
        id: "signing-in",
        title: "Quando você entra",
        blocks: [
          'Qualquer pessoa pode entrar com um e-mail, para ver o que doou ou para entrar em uma lista (abaixo). Guardamos o endereço e quando ele entrou pela última vez, tenha doado ou não, até você pedir que o apaguemos. Cada link e código de acesso funciona uma vez, por 15 minutos; guardamos só um hash irreversível deles, nunca o link ou o código em si, e, num link pedido pelo "Me avise", a lista a que ele se refere.',
        ],
      },
      {
        id: "lists",
        title: "Quando você pede para ser avisado",
        blocks: [
          'Qualquer pessoa pode pedir para saber quando uma cidade for construída, ou quando novas cidades forem construídas, clicando em "Me avise" e confirmando o e-mail com um link ou código de acesso. Para cada lista em que você entra, guardamos, junto do seu endereço, qual é a lista, quando você entrou e o idioma da página de onde entrou. Enviamos:',
          {
            items: [
              "um e-mail de boas-vindas, com três prédios das cidades que já estão no mapa;",
              'um e-mail quando uma cidade que você espera for construída; para "novas cidades", um sempre que um novo mundo for adicionado ao mapa.',
            ],
          },
          'Nada mais é enviado pelas listas. A lista de uma cidade termina quando o e-mail dela é enviado; "novas cidades" dura até você sair.',
          'Você pode sair a qualquer momento: todo e-mail de lista tem um link "Sair desta lista" que não pede acesso, e o botão de cancelar inscrição do seu programa de e-mail também funciona. Você também pode sair de qualquer lista em [Suas cidades](me).',
          "Sair de uma lista tira você dela, e só: seu endereço continua guardado, como em qualquer acesso (acima), com um registro dos e-mails de lista enviados a ele (quando foi o de boas-vindas, e de qual cidade cada um dos outros falava, e quando), para que ninguém seja avisado duas vezes. Os dois ficam até você pedir que os apaguemos.",
        ],
      },
      {
        id: "sharing",
        title: "Quem mais vê esses dados",
        blocks: [
          "Não vendemos nem alugamos dados pessoais, e não os compartilhamos para publicidade. Além de nós, eles chegam apenas a:",
          {
            items: [
              { term: "Stripe,", text: "para os pagamentos." },
              { term: "Google,", text: "para o Google Analytics, onde é permitido (acima)." },
              {
                term: "Os servidores em que o site roda,",
                text: "para o site, o banco de dados, os e-mails e o Rybbit. Como qualquer servidor web, eles veem seu endereço IP e seu navegador e os registram em logs técnicos, usados só para manter e proteger o site; a API também usa os endereços por um instante, para limitar com que frequência algo pode ser pedido a ela.",
              },
              { term: "As autoridades,", text: "se a lei exigir." },
            ],
          },
          "O Stripe e o Google podem tratar dados fora do seu país, inclusive nos Estados Unidos.",
        ],
      },
      {
        id: "keeping",
        title: "Por quanto tempo guardamos",
        blocks: [
          {
            items: [
              {
                term: "Doações:",
                text: "enquanto o site existir: elas formam o financiamento de cada cidade e sua lista de benfeitores, e são o registro dos pagamentos.",
              },
              {
                term: "Seu endereço e seus dados de doador",
                text: "(e-mail, nome, links), tenha você doado ou apenas entrado: até você pedir que os apaguemos.",
              },
              {
                term: "Visitas e respostas do quiz:",
                text: "guardadas, sem nada que aponte para você, para contar visitas e respostas ao longo do tempo.",
              },
              {
                term: "Participação em listas:",
                text: "até você sair da lista ou, no caso de uma cidade, até o e-mail dela ser enviado.",
              },
              {
                term: "Registro dos e-mails de lista",
                text: "(de qual cidade cada um falava e quando, e quando foi o de boas-vindas): até você pedir que apaguemos seu endereço, para que ninguém seja avisado duas vezes.",
              },
              {
                term: "Links e códigos de acesso:",
                text: "só como hashes irreversíveis, com a lista para a qual o link foi pedido, se houver: por um dia ou até aquele endereço pedir outro, o que vier depois.",
              },
            ],
          },
        ],
      },
      {
        id: "rights",
        title: "Seus direitos",
        blocks: [
          "Dependendo de onde você mora (a LGPD no Brasil, o RGPD na Europa e no Reino Unido, a CCPA na Califórnia, entre outras), você pode ter o direito de saber o que guardamos sobre você, de receber uma cópia, de pedir correção ou exclusão e de se opor ao uso desses dados. Onde quer que você more, escreva para {email} a partir do endereço em questão e faremos isso em até 30 dias. Se apagarmos seus dados de doador, suas doações continuam contadas nas cidades, sem nada que as ligue a você. Você também pode reclamar à autoridade de proteção de dados (no Brasil, a ANPD).",
          "Onde a lei pede uma base legal: manter o site e contar suas visitas são do nosso legítimo interesse; registrar uma doação e escrever a você sobre ela são necessários para a doação que você fez; os cookies do Google Analytics, e mostrar seu nome e seus links, dependem do seu consentimento, que você pode retirar a qualquer momento.",
        ],
      },
      {
        id: "children",
        title: "Crianças",
        blocks: [
          "Os mapas são para todos. Doar e entrar são para adultos: se você tem menos de 18 anos, peça antes a um dos pais ou responsável. Não coletamos de propósito dados pessoais de crianças com menos de 13 anos; se achar que coletamos, escreva para nós e os apagaremos.",
        ],
      },
      {
        id: "changes",
        title: "Mudanças",
        blocks: [
          "Quando esta política mudar, atualizaremos a data no topo e, se a mudança importar para os doadores, os avisaremos por e-mail. Ela foi escrita em inglês e traduzida para o português, o espanhol e o chinês simplificado; se uma tradução divergir do texto em inglês, vale o texto em inglês.",
        ],
      },
    ],
  },

  "es-ES": {
    title: "Política de privacidad",
    description:
      "Qué recoge tinyworlds cuando exploras, donas o inicias sesión, para qué, quién más lo ve y cómo pedir que se corrija o se borre.",
    summary: [
      "Puedes explorar todos los mapas sin decirnos quién eres. No vendemos datos ni mostramos anuncios.",
      "Las visitas se cuentan con un código aleatorio que vive en una pestaña del navegador, no con cookies.",
      "Las cookies de Google Analytics solo se usan donde está permitido: en Europa preguntamos antes, y cualquiera puede decir que no.",
      "Si donas, Stripe gestiona el pago; guardamos tu correo, lo que donaste y, si quieres, tu nombre en una ciudad.",
      "Si pides que te avisemos cuando se construya una ciudad, guardamos tu correo en esa lista y enviamos solo sus correos; todos traen un enlace para darte de baja.",
      "Escribe a {email} para ver, corregir o borrar lo que tenemos sobre ti.",
    ],
    sections: [
      {
        id: "who",
        title: "Quiénes somos",
        blocks: [
          "tinyworlds es un pequeño proyecto independiente, hecho y mantenido por una persona. Nosotros decidimos qué se recoge en este sitio y para qué, y puedes escribirnos a {email}. Esta política vale para todo el sitio: la página principal, todos los mapas, Tus ciudades y el Salón de la Fama.",
        ],
      },
      {
        id: "exploring",
        title: "Cuando exploras",
        blocks: [
          "Explorar no pide cuenta, y los mapas nunca preguntan quién eres. Para saber qué mapas se visitan, y cómo llegó una donación, cada página le dice a nuestro servidor:",
          {
            items: [
              {
                term: "Que se abrió:",
                text: "qué mapa y el idioma de la interfaz, con un código aleatorio que la página se inventa y guarda en el almacenamiento de sesión de esa pestaña. El código desaparece al cerrar la pestaña; no es una cookie y no dice nada de quién eres.",
              },
              {
                term: "Que un mapa sigue abierto:",
                text: "cada 30 segundos mientras está a la vista, para que el sitio pueda decir cuántas personas están explorando ahora y nosotros, cuánto duran las visitas.",
              },
              {
                term: "Los pasos hacia una donación:",
                text: "abrir el panel de donación, empezar un pago o volver de él sin pagar, con la ciudad y el importe elegidos.",
              },
              {
                term: "Tus respuestas en Adivina el edificio:",
                text: "el edificio preguntado, el nombre que elegiste, la dificultad y el idioma, para que todos vean cómo respondieron los demás.",
              },
            ],
          },
          "Nada de esto incluye tu nombre, tu correo ni tu dirección IP. Si después donas, la donación guarda el código de la visita, lo que une los pasos de esa visita a tu donación. No contamos las visitas de un navegador en el que ha entrado uno de nuestros administradores, ni las de un navegador manejado por un programa.",
        ],
      },
      {
        id: "analytics",
        title: "Estadísticas y cookies",
        blocks: [
          "Usamos dos herramientas de estadísticas para saber cómo encuentra y usa la gente los mapas:",
          {
            items: [
              {
                term: "Rybbit,",
                text: "que mantenemos nosotros mismos. Cuenta las páginas vistas y algunos clics (abrir el panel de donación, empezar el juego), con la página, el sitio del que vienes, tu navegador, dispositivo e idioma, y el país y la región deducidos de tu dirección IP. No usa cookies.",
              },
              {
                term: "Google Analytics,",
                text: "a través de Google Tag Manager. Este sí usa cookies (_ga), y Google trata lo que recoge según su propia [política de privacidad](google). Si tu navegador está en una zona horaria de Europa (o la oculta), preguntamos antes, y no se carga nada de Google hasta que dices que sí; en el resto del mundo se carga con la página, con todo lo relacionado con la publicidad desactivado. Estés donde estés, puedes decir que no en cualquier momento en [Configuración de cookies](cookies), en la página principal o en el «Acerca de» de un mapa: el no se recuerda, y las cookies de Google se borran.",
              },
            ],
          },
          "Ninguna página del sitio usa cookies para publicidad.",
        ],
      },
      {
        id: "your-browser",
        title: "Lo que se queda en tu navegador",
        blocks: [
          "El sitio guarda algunas cosas en el almacenamiento de tu navegador, que solo salen de él como se explica arriba:",
          {
            items: [
              "tus ajustes (idioma, tema, las opciones del mapa) y tu respuesta sobre las cookies;",
              "los edificios que ya has jugado en Adivina el edificio, y la partida que elegiste;",
              "el código de la visita, y lo que elegiste en el panel de donación mientras estás en el pago (en el almacenamiento de sesión, que desaparece al cerrar la pestaña);",
              "si inicias sesión, tu sesión, durante 7 días;",
              "si pulsas «Avísame» antes de iniciar sesión, qué lista pediste y cuándo (en tinyworlds.join): se lee y se borra cuando termina el inicio de sesión, y se ignora si tiene más de una hora.",
            ],
          },
          "Puedes borrarlas cuando quieras en los ajustes del navegador.",
        ],
      },
      {
        id: "donating",
        title: "Cuando donas",
        blocks: [
          "Los pagos los gestiona Stripe, según su [política de privacidad](stripe). Stripe recoge los datos de tu tarjeta u otro medio de pago y tu correo; nosotros nunca vemos los datos de tu tarjeta. De Stripe recibimos y guardamos:",
          {
            items: [
              "tu dirección de correo;",
              "el importe, la moneda, la fecha y las referencias de Stripe para el pago;",
              "la ciudad que propusiste, el mapa del que venías, el idioma y el código de la visita;",
              "el nombre con el que pediste aparecer, si lo pediste.",
            ],
          },
          "Usamos tu correo para darte las gracias por la donación, para avisarte cuando se construya una ciudad a la que donaste y para decirte, una vez, que estás en el Salón de la Fama; la persona que hace tinyworlds también puede escribirte una vez, desde su propia dirección, para darte las gracias en persona. No enviamos boletines ni publicidad más allá de las listas a las que pides unirte (abajo), y tu correo nunca se muestra en el sitio, a nadie.",
        ],
      },
      {
        id: "public",
        title: "Lo que es público",
        blocks: [
          "El importe y las piedras de una donación se muestran en la lista de su ciudad sin decir quién donó, salvo que pidas aparecer con tu nombre: entonces tu nombre aparece ahí, en la página principal y en el Salón de la Fama, con los enlaces que añadas. Puedes dejar de aparecer, cambiar tu nombre o quitar tus enlaces cuando quieras en [Tus ciudades](me). Podemos ocultar un nombre o unos enlaces que incumplan nuestras [Condiciones de uso](terms).",
          "Para mostrar un enlace, nuestro servidor lee la página enlazada una vez (y otra vez cuando lo pidas) y guarda su título, su descripción y copias pequeñas de su icono y su imagen, para que los navegadores de los visitantes nunca contacten con el sitio enlazado.",
        ],
      },
      {
        id: "signing-in",
        title: "Cuando inicias sesión",
        blocks: [
          "Cualquiera puede iniciar sesión con un correo, para ver lo que ha donado o para unirse a una lista (abajo). Guardamos la dirección y cuándo entró por última vez, haya donado o no, hasta que nos pidas borrarla. Cada enlace y código de acceso sirve una vez, durante 15 minutos; solo guardamos un hash irreversible de ellos, nunca el enlace ni el código, y, en un enlace pedido desde «Avísame», la lista a la que corresponde.",
        ],
      },
      {
        id: "lists",
        title: "Cuando pides que te avisemos",
        blocks: [
          "Cualquiera puede pedir que le avisen cuando se construya una ciudad, o cuando se construyan ciudades nuevas, pulsando «Avísame» y confirmando su correo con un enlace o código de acceso. De cada lista a la que te unes guardamos, junto a tu dirección, qué lista es, cuándo te uniste y el idioma de la página desde la que lo hiciste. Enviamos:",
          {
            items: [
              "un correo de bienvenida, con tres edificios de las ciudades que ya están en el mapa;",
              "un correo cuando se construya una ciudad que esperas; para «ciudades nuevas», uno cada vez que se añada un mundo nuevo al mapa.",
            ],
          },
          "Por las listas no se envía nada más. La lista de una ciudad termina cuando se ha enviado su correo; «ciudades nuevas» dura hasta que te des de baja.",
          "Puedes darte de baja cuando quieras: todo correo de una lista trae un enlace «Salir de esta lista» que no pide iniciar sesión, y el botón de cancelar suscripción de tu programa de correo también funciona. También puedes salir de cualquier lista en [Tus ciudades](me).",
          "Salir de una lista te quita de ella, y nada más: tu dirección sigue guardada, como en cualquier inicio de sesión (arriba), con una nota de los correos de las listas que se le enviaron (cuándo salió el de bienvenida, y de qué ciudad hablaba cada uno de los demás, y cuándo), para que a nadie se le avise dos veces. Ambas se guardan hasta que nos pidas borrarlas.",
        ],
      },
      {
        id: "sharing",
        title: "Quién más lo ve",
        blocks: [
          "No vendemos ni alquilamos datos personales, y no los compartimos para publicidad. Aparte de nosotros, solo llegan a:",
          {
            items: [
              { term: "Stripe,", text: "para los pagos." },
              { term: "Google,", text: "para Google Analytics, donde está permitido (arriba)." },
              {
                term: "Los servidores en los que funciona el sitio,",
                text: "para el sitio, su base de datos, su correo y Rybbit. Como cualquier servidor web, ven tu dirección IP y tu navegador y los anotan en registros técnicos, que solo sirven para mantener y proteger el sitio; la API también usa las direcciones un instante, para limitar cuántas veces se le puede pedir algo.",
              },
              { term: "Las autoridades,", text: "si la ley lo exige." },
            ],
          },
          "Stripe y Google pueden tratar datos fuera de tu país, incluso en Estados Unidos.",
        ],
      },
      {
        id: "keeping",
        title: "Cuánto tiempo lo guardamos",
        blocks: [
          {
            items: [
              {
                term: "Donaciones:",
                text: "mientras exista el sitio: forman los fondos de cada ciudad y su lista de benefactores, y son el registro de los pagos.",
              },
              {
                term: "Tu dirección y tus datos de donante",
                text: "(correo, nombre, enlaces), hayas donado o solo iniciado sesión: hasta que nos pidas borrarlos.",
              },
              {
                term: "Visitas y respuestas del juego:",
                text: "se guardan, sin nada que apunte a ti, para contar visitas y respuestas a lo largo del tiempo.",
              },
              {
                term: "Pertenencia a listas:",
                text: "hasta que salgas de la lista o, en el caso de una ciudad, hasta que se haya enviado su correo.",
              },
              {
                term: "Nota de los correos de las listas",
                text: "(de qué ciudad hablaba cada uno y cuándo, y cuándo salió el de bienvenida): hasta que nos pidas borrar tu dirección, para que a nadie se le avise dos veces.",
              },
              {
                term: "Enlaces y códigos de acceso:",
                text: "solo como hashes irreversibles, con la lista para la que se pidió el enlace, si la hay: un día o hasta que esa dirección pida otro, lo que ocurra más tarde.",
              },
            ],
          },
        ],
      },
      {
        id: "rights",
        title: "Tus derechos",
        blocks: [
          "Según dónde vivas (el RGPD en Europa y el Reino Unido, la LGPD en Brasil, la CCPA en California, entre otras), puedes tener derecho a saber qué guardamos sobre ti, a recibir una copia, a que se corrija o se borre y a oponerte a su uso. Vivas donde vivas, escribe a {email} desde la dirección en cuestión y lo haremos en un plazo de 30 días. Si borramos tus datos de donante, tus donaciones siguen contando en sus ciudades, sin nada que las una a ti. También puedes reclamar ante tu autoridad de protección de datos (en España, la AEPD).",
          "Donde la ley pide una base jurídica: mantener el sitio y contar sus visitas es nuestro interés legítimo; registrar una donación y escribirte sobre ella es necesario para la donación que hiciste; las cookies de Google Analytics, y mostrar tu nombre y tus enlaces, se basan en tu consentimiento, que puedes retirar en cualquier momento.",
        ],
      },
      {
        id: "children",
        title: "Menores",
        blocks: [
          "Los mapas son para todo el mundo. Donar e iniciar sesión es cosa de adultos: si tienes menos de 18 años, pregunta antes a tu madre, padre o tutor. No recogemos a sabiendas datos personales de menores de 13 años; si crees que lo hemos hecho, escríbenos y los borraremos.",
        ],
      },
      {
        id: "changes",
        title: "Cambios",
        blocks: [
          "Cuando esta política cambie, actualizaremos la fecha de arriba y, si el cambio importa a los donantes, se lo diremos por correo. Está escrita en inglés y traducida al portugués, al español y al chino simplificado; si una traducción difiere del texto en inglés, prevalece el texto en inglés.",
        ],
      },
    ],
  },
  "zh-CN": {
    title: "隐私政策",
    description:
      "当你探索、捐助或登录时，tinyworlds 收集什么、为什么收集、还有谁能看到，以及如何更正或删除这些信息。",
    summary: [
      "你可以探索每一张地图，而不必告诉我们你是谁。我们不出售数据，也不展示广告。",
      "访问量用一个只存在于单个浏览器标签页中的随机 ID 来统计，而不是用 Cookie。",
      "Google Analytics 的 Cookie 只在获得允许的地方使用：在欧洲我们会先征求同意，任何人都可以拒绝。",
      "如果你捐助，付款由 Stripe 处理；我们保存你的邮箱、你捐了多少，以及（如果你选择的话）你在城市上的名字。",
      "如果你要求在某座城市建成时收到通知，我们会把你的邮箱保存在那份名单上，只发送与之相关的邮件；每封邮件都有退出的链接。",
      "写信到 {email}，即可查看、更正或删除我们保存的关于你的信息。",
    ],
    sections: [
      {
        id: "who",
        title: "我们是谁",
        blocks: [
          "tinyworlds 是一个由一个人制作和运营的小型独立项目。我们决定本网站收集什么以及为什么收集，你可以通过 {email} 联系我们。本政策适用于整个网站：首页、每张地图、“你的城市”和名人堂。",
        ],
      },
      {
        id: "exploring",
        title: "当你探索时",
        blocks: [
          "探索不需要账号，地图也从不询问你是谁。为了让我们知道哪些地图被访问过，以及一笔捐助是怎么来的，每个页面会告诉我们的服务器：",
          {
            items: [
              {
                term: "页面被打开了：",
                text: "哪张地图，以及界面语言，附带一个由页面随机生成并保存在该标签页会话存储中的 ID。标签页关闭后这个 ID 就消失了；它不是 Cookie，也不透露你是谁。",
              },
              {
                term: "地图仍然开着：",
                text: "地图在视野中时每 30 秒一次，这样网站就能显示现在有多少人在探索，我们也能看到访问持续多久。",
              },
              {
                term: "走向捐助的各个步骤：",
                text: "打开捐助面板、开始结账，或未付款就从结账页面返回，附带所选的城市和金额。",
              },
              {
                term: "你在“猜建筑”中的答案：",
                text: "被问到的建筑、你选的名字、难度和语言，这样每个人都能看到其他人是怎么回答的。",
              },
            ],
          },
          "这些信息都不包含你的名字、邮箱或 IP 地址。如果你之后捐助，捐助会保留这次访问的 ID，从而把这次访问的各个步骤与你的捐助联系起来。来自已登录我们管理员账号的浏览器的访问，或来自由程序驱动的浏览器的访问，不会被计入。",
        ],
      },
      {
        id: "analytics",
        title: "分析工具与 Cookie",
        blocks: [
          "我们使用两种分析工具来了解人们如何找到和使用这些地图：",
          {
            items: [
              {
                term: "Rybbit，",
                text: "由我们自己运行。它统计页面浏览量和少数几种点击（打开捐助面板、开始游戏），附带页面、你来自的网站、你的浏览器、设备和语言，以及根据你的 IP 地址推算的国家和地区。它不设置任何 Cookie。",
              },
              {
                term: "Google Analytics，",
                text: "通过 Google Tag Manager 加载。它会设置 Cookie（_ga），Google 按其自身的[隐私政策](google)处理它收集的信息。如果你的浏览器设置为欧洲时区（或隐藏了时区），我们会先征求你的同意，在你同意之前不会加载任何来自 Google 的内容；在其他地区，它会随页面加载，所有与广告有关的功能都已关闭。无论你在哪里，都可以随时在首页或地图的“关于”中的[Cookie 设置](cookies)里拒绝：拒绝会被记住，Google 的 Cookie 也会被删除。",
              },
            ],
          },
          "本网站没有任何页面将 Cookie 用于广告。",
        ],
      },
      {
        id: "your-browser",
        title: "保存在你浏览器中的内容",
        blocks: [
          "本网站会在你浏览器的存储中保存几样东西，除上文所述外，它们永远不会离开你的浏览器：",
          {
            items: [
              "你的设置（语言、主题、地图的各个开关）以及你对 Cookie 问题的回答；",
              "你在“猜建筑”中已经玩过的建筑，以及你选择的游戏；",
              "访问的 ID，以及你在结账期间于捐助面板中的选择（保存在会话存储中，标签页关闭后即消失）；",
              "如果你登录，你的会话，保存 7 天；",
              "如果你在登录前点击了“通知我”，你要求加入哪份名单以及何时（保存在 tinyworlds.join 下）：登录完成后它会被读取并删除，超过一小时则被忽略。",
            ],
          },
          "你可以随时在浏览器设置中清除它们。",
        ],
      },
      {
        id: "donating",
        title: "当你捐助时",
        blocks: [
          "付款由 Stripe 处理，适用其[隐私政策](stripe)。Stripe 收集你的银行卡或其他付款信息以及你的邮箱地址；我们永远看不到你的银行卡信息。我们从 Stripe 接收并保存：",
          {
            items: [
              "你的邮箱地址；",
              "金额、币种、日期以及 Stripe 对这笔付款的参考编号；",
              "你提名的城市、你来自的地图、语言和访问的 ID；",
              "你要求署名的名字（如果有的话）。",
            ],
          },
          "我们用你的邮箱地址感谢你的捐助，在你捐助的城市建成时通知你，并告诉你一次你已进入名人堂；tinyworlds 的制作者也可能用他自己的邮箱给你写一封信，亲自向你道谢。除了你主动要求加入的名单（见下文），我们不发送任何简报或营销邮件，你的邮箱地址也永远不会在网站上向任何人显示。",
        ],
      },
      {
        id: "public",
        title: "哪些内容是公开的",
        blocks: [
          "一笔捐助的金额和砖会显示在其城市的名单上，但不会说明是谁捐的，除非你要求署名：那样你的名字会和你添加的链接一起显示在那里、首页和名人堂中。你可以随时在[你的城市](me)页面取消署名、修改名字或移除链接。违反我们[服务条款](terms)的名字或链接，我们可能会隐藏。",
          "为了显示链接，我们的服务器会读取一次所链接的页面（之后每当你要求时再读取），并保存它的标题、描述，以及图标和配图的小尺寸副本，这样访客的浏览器就永远不会联系被链接的网站。",
        ],
      },
      {
        id: "signing-in",
        title: "当你登录时",
        blocks: [
          "任何人都可以用邮箱地址登录，查看自己捐了什么，或加入一份名单（见下文）。无论是否捐助过，我们都会保存该地址及其最后一次登录的时间，直到你要求我们删除。每个登录链接和验证码只能使用一次，有效期 15 分钟；我们只保存它们的单向哈希值，从不保存链接或验证码本身；如果链接是通过“通知我”申请的，还会保存它对应的名单。",
        ],
      },
      {
        id: "lists",
        title: "当你要求接收通知时",
        blocks: [
          "任何人都可以点击“通知我”，并通过登录链接或验证码确认自己的邮箱地址，要求在某座城市建成时、或有新城市建成时收到通知。对于你加入的每份名单，除了你的地址，我们还会保存是哪份名单、你何时加入，以及你加入时所在页面的语言。我们会发送：",
          {
            items: [
              "一封欢迎邮件，附上已在地图上的城市中的三座建筑；",
              "你等待的城市建成时的一封邮件；对于“新城市”名单，每当有新世界加入地图时发送一封。",
            ],
          },
          "名单不会发送任何其他内容。一座城市的名单在其通知邮件发出后即结束；“新城市”名单则持续到你退出为止。",
          "你可以随时退出：每封名单邮件都有一个无需登录的“退出此名单”链接，你邮件程序自带的退订按钮也同样有效。你也可以在[你的城市](me)页面退出任何名单。",
          "退出只会把你从名单中移除，仅此而已：你的地址会像任何登录一样保留（见上文），并附有发给它的名单邮件记录（欢迎邮件何时发出，其他每封邮件是关于哪座城市以及何时发出），以免有人收到两次通知。两者都会保留到你要求我们删除为止。",
        ],
      },
      {
        id: "sharing",
        title: "还有谁能看到",
        blocks: [
          "我们不出售或出租个人数据，也不为广告目的分享个人数据。除了我们，只有以下各方会接触到这些数据：",
          {
            items: [
              { term: "Stripe，", text: "用于付款。" },
              { term: "Google，", text: "用于 Google Analytics，仅在获得允许的地方（见上文）。" },
              {
                term: "运行本网站的服务器，",
                text: "用于网站、数据库、邮件和 Rybbit。和任何 Web 服务器一样，它们能看到你的 IP 地址和浏览器，并将其写入技术日志，仅用于运行和保护网站；API 也会短暂使用这些地址，以限制向它发出请求的频率。",
              },
              { term: "有关当局，", text: "如果法律要求。" },
            ],
          },
          "Stripe 和 Google 可能会在你所在国家以外（包括美国）处理数据。",
        ],
      },
      {
        id: "keeping",
        title: "我们保存多久",
        blocks: [
          {
            items: [
              {
                term: "捐助：",
                text: "在网站运行期间一直保存：它们构成了每座城市的筹款和捐助者名单，也是付款的记录。",
              },
              {
                term: "你的地址和捐助者信息",
                text: "（邮箱地址、名字、链接），无论你是捐助过还是只登录过：保存到你要求我们删除为止。",
              },
              {
                term: "访问记录和答题答案：",
                text: "会被保存，但其中没有任何指向你的信息，用于长期统计访问量和答案。",
              },
              {
                term: "名单成员资格：",
                text: "保存到你退出名单为止；对于某座城市的名单，保存到其通知邮件发出为止。",
              },
              {
                term: "名单邮件记录",
                text: "（每封邮件是关于哪座城市以及何时发出，欢迎邮件何时发出）：保存到你要求我们删除你的地址为止，以免有人收到两次通知。",
              },
              {
                term: "登录链接和验证码：",
                text: "只以单向哈希值的形式保存，如有申请链接时对应的名单也一并保存：保存一天，或保存到该地址下次申请为止，以较晚者为准。",
              },
            ],
          },
        ],
      },
      {
        id: "rights",
        title: "你的权利",
        blocks: [
          "根据你所在的地区（例如欧洲和英国的 GDPR、巴西的 LGPD、加州的 CCPA 等），你可能有权了解我们保存了你的哪些信息、获取一份副本、要求更正或删除，以及反对其使用。无论你住在哪里，只要用相关的邮箱地址写信到 {email}，我们都会在 30 天内处理。如果我们删除了你的捐助者信息，你的捐助仍会计入相应的城市，但不会再留下任何与你相关的信息。你也可以向你所在地的数据保护机构投诉。",
          "在法律要求提供法律依据的情况下：运行网站和统计访问量属于我们的正当利益；记录捐助并就此与你联系，是履行你所做捐助的需要；Google Analytics 的 Cookie，以及显示你的名字和链接，基于你的同意，你可以随时撤回。",
        ],
      },
      {
        id: "children",
        title: "儿童",
        blocks: [
          "地图适合所有人。捐助和登录仅限成年人：如果你未满 18 岁，请先征得父母或监护人的同意。我们不会故意收集 13 岁以下儿童的个人信息；如果你认为我们收集了，请写信给我们，我们会将其删除。",
        ],
      },
      {
        id: "changes",
        title: "变更",
        blocks: [
          "本政策变更时，我们会更新顶部的日期；如果变更对捐助者有影响，我们会通过邮件告知他们。本政策以英文撰写，并翻译成葡萄牙语、西班牙语和简体中文；如果译文与英文文本有任何不一致，以英文文本为准。",
        ],
      },
    ],
  },
};
